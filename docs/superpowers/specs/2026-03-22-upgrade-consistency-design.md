# Upgrade System Consistency Overhaul

**Date:** 2026-03-22
**Status:** Draft
**Scope:** upgrade.sh, upgrade-helper-process, utilities_system.py, utilities.html, utilities.js, Caddy config, install.sh

## Problem Statement

The upgrade system has three independent code paths (CLI via upgrade.sh, web UI via API + helper, remote via SSH) that have drifted out of sync. Critical bugs and gaps include:

1. **Service name bug:** `scripts/upgrade-helper-process` uses `audiobooks-*` (plural) for every service name, but actual systemd units are `audiobook-*` (singular). ALL web-triggered service operations silently fail — start, stop, restart, upgrade lifecycle.

2. **Feature gap:** upgrade.sh supports `--force`, `--major-version`, `--version`, `--backup`, `--dry-run`, `--switch-to-*` — the web UI only exposes source selection (GitHub/project) and check/upgrade. A web-only admin has no access to force reinstall, major version upgrade, specific version install, or architecture switching.

3. **Redundant lifecycle:** The helper stops services, then calls upgrade.sh which stops them again internally. Two owners of the same lifecycle, neither fully in control.

4. **No preflight gate:** Upgrades can be triggered without any prior check. No verification that the admin reviewed what's about to happen, especially dangerous for major version upgrades that rebuild the venv, migrate config, and enable new services.

5. **Fragile browser UX:** When the API restarts during upgrade, the polling JS hits fetch errors and shows error toasts instead of gracefully waiting for the API to come back.

6. **No external maintenance page:** When all audiobook services are down during upgrade, external visitors hitting the site get a generic Cloudflare 502 error with no indication that an upgrade is in progress.

## Design Decisions

### Mandatory backup on every upgrade

Backup is not an option — it always happens. The `--backup` flag is accepted but is a no-op (backward compatibility). Both `backup_auth_db()` and `create_backup()` run unconditionally on every upgrade, CLI and web. Rolling retention: keep last 5 installation backups, delete older.

**Rationale:** Nobody ever regretted having a backup. The cost is a few seconds of `cp -a`; the cost of not having one during a failed upgrade is potentially catastrophic.

### Mandatory preflight check (LEAPP-inspired)

Modeled after Red Hat's LEAPP upgrade process. Upgrades require a completed preflight check before execution.

**Phase 1 — Preflight check:**
- Runs full dry-run analysis
- Writes durable report to `/var/lib/audiobooks/.control/upgrade-preflight.json`
- Report contains: timestamp, source, current version, target version, major/minor determination, config keys that will be added/changed/removed, files that will change, venv rebuild needed (yes/no), new services to be enabled, disk space check, warnings
- For major version upgrades: explicitly lists config impacts and expected additional downtime (~60s for venv rebuild)

**Phase 2 — Upgrade execution:**
- Verifies preflight file exists, is recent (< 30 minutes), and matches current request (same source, same target version)
- Re-runs preflight checks internally as first step — if conditions changed since preflight (config modified, new version appeared, disk space changed), aborts with explanation of what drifted
- If preflight missing or stale: refuses to proceed, tells admin to run check first

**Exception — `--force`:**
- Bypasses: preflight requirement, version-match check, major version confirmation, staleness window
- Does NOT bypass: backup, actual upgrade mechanics, status reporting
- Both CLI and web: prominent warning listing everything being bypassed, requires explicit confirmation ("Type YES" on CLI, danger-styled confirmation modal on web)

**Preflight file as audit trail:** The preflight report persists and documents what the admin saw before approving. Useful for post-incident review.

### Lifecycle ownership: helper owns services, upgrade.sh owns files

**New flag: `--skip-service-lifecycle`** (internal, not shown in `--help`)

When set, upgrade.sh skips: `stop_services()`, `start_services()`, the `_cleanup_on_exit` safety trap, and service status display. It becomes a pure "apply files + venv + migrations + permissions + audit" tool.

When not set (default, CLI usage): upgrade.sh manages the full lifecycle as it always has.

The helper passes `--skip-service-lifecycle --yes` to upgrade.sh and owns the complete orchestrated lifecycle:

| Step | Stage value | Status message | Description |
|------|-------------|----------------|-------------|
| 1 | `preflight_recheck` | "Re-validating preflight..." | Re-run preflight validation; abort if conditions drifted |
| 2 | `backing_up` | "Backing up installation..." | Run `backup_auth_db` + `create_backup` |
| 3 | `stopping_services` | "Stopping services..." | Stop in order: downloader.timer, shutdown-saver, scheduler, mover, converter, redirect, proxy, API |
| 4 | `upgrading` | "Upgrading files..." | Run upgrade.sh with `--skip-service-lifecycle --yes` (+ `--force`, `--major-version`, `--version X` as applicable) |
| 5 | `rebuilding_venv` | "Rebuilding virtual environment..." | Only for major version (upgrade.sh handles internally via `--major-version`) |
| 6 | `migrating_config` | "Migrating configuration..." | Only for major version (upgrade.sh handles internally via `--major-version`) |
| 7 | `starting_services` | "Starting services..." | Start in reverse order: API, proxy, redirect, converter, mover, scheduler, shutdown-saver, downloader.timer |
| 8 | `verifying` | "Verifying upgrade..." | Poll `/api/system/health` until responding (max 30s) |
| 9 | `complete` | "Upgrade completed successfully" | Write final status with result details |

**Service stop/start order includes ALL services in `audiobook.target`:** API, proxy, redirect, converter, mover, downloader.timer, scheduler, shutdown-saver. Not just the "controllable" subset. During an upgrade, everything stops and restarts.

**Note on steps 5-6 (venv/config):** These are executed by upgrade.sh internally when `--major-version` is passed. The helper doesn't run them separately — it reports them as distinct stages by parsing upgrade.sh's output for venv/config progress markers. If `--major-version` is not set, these stages are skipped in the progress display.

**No separate "Restart API" step.** In the previous design, the API was started in one step then restarted in a later step — this was redundant. In this design, all services (including the API) are stopped in step 3. Upgrade.sh deploys new code in step 4. The API is started fresh with the new code in step 7. No restart needed — the new code is already in place before the API starts.

**Staleness thresholds:** The server-side preflight gate uses a 30-minute staleness window (how long a preflight report remains valid for triggering an upgrade). The browser-side JS uses a 10-minute window (how long before the UI warns the admin to re-check). The browser threshold is intentionally shorter to encourage re-checking before the server would hard-reject, avoiding a surprise "preflight expired" error at upgrade time.

Removes the `echo "y" |` pipe hack — `--yes` flag handles confirmation properly.

## Section 1: Service Name Bug Fix

### Files affected

`scripts/upgrade-helper-process`

### Changes

Replace every occurrence of `audiobooks-*` (plural) with `audiobook-*` (singular):

| Wrong (current) | Correct |
|---|---|
| `audiobooks-api` | `audiobook-api` |
| `audiobooks-proxy` | `audiobook-proxy` |
| `audiobooks-converter` | `audiobook-converter` |
| `audiobooks-mover` | `audiobook-mover` |
| `audiobooks-downloader.timer` | `audiobook-downloader.timer` |

This is a global find-and-replace across the entire file — every instance of `audiobooks-` becomes `audiobook-`. Key locations include:
- `VALID_SERVICES` array (line 31-37)
- `do_services_stop_all()` stop_order array (lines 210-213)
- `do_services_stop_all()` include_api stop_order (line 217)
- `do_upgrade()` services_to_stop array (lines 360-363)
- `do_upgrade()` API restart (line 468-469)
- Comments/documentation (lines 13-17)

**Note on `/usr/local/bin/audiobooks-upgrade` fallback (lines 263, 381):** The helper falls back to `audiobooks-upgrade` (plural) as a CLI binary name. This is intentional — the installed wrapper script is named `audiobook-upgrade` (singular, matching the `audiobook-*` convention). The fallback path should be corrected to `/usr/local/bin/audiobook-upgrade`. This is the same bug manifesting in the binary name lookup.

### Verification

After fix, from the web UI:
- Service start/stop/restart actually affects the real systemd units
- Upgrade check runs and returns results
- Upgrade execution stops real services, upgrades, restarts real services

## Section 2: `--skip-service-lifecycle` Flag

### upgrade.sh changes

New flag `SKIP_SERVICE_LIFECYCLE=false`, set by `--skip-service-lifecycle` in argument parser.

When `SKIP_SERVICE_LIFECYCLE=true`:
- Main block (lines 2127-2139): skip `stop_services`, `start_services`, and the `_SERVICES_STOPPED` tracking
- `do_github_upgrade()`: skip `stop_services`, `start_services`, auth validation (helper handles these)
- `_cleanup_on_exit` trap: skip service restart (helper owns lifecycle)
- `do_upgrade()` function: unchanged (it doesn't manage services directly)

When `SKIP_SERVICE_LIFECYCLE=false` (default): existing behavior preserved for CLI users.

Flag is NOT shown in `--help` output — it's internal plumbing for the helper.

### upgrade-helper-process changes

- Pass `--skip-service-lifecycle --yes` to upgrade.sh
- Parse new request fields: `force`, `major_version`, `version`, from request JSON
- Build upgrade.sh command with corresponding flags
- Own the full service lifecycle with status updates at each stage

### Backup always-on

In upgrade.sh:
- `create_backup()` runs unconditionally in both the project-based main block and `do_github_upgrade()`
- `backup_auth_db()` already runs unconditionally — no change needed
- `--backup` flag is accepted but ignored (no-op for backward compat)
- `create_backup()` implements rolling retention: keep last 5 backups matching `${target}.backup.*`, delete older

In helper:
- Backup stage is part of the lifecycle, reported via status updates
- No flag needed — backup always happens

## Section 3: Full Feature Parity — Web UI

### API endpoint changes (`utilities_system.py`)

**`POST /api/system/upgrade/check`** — new optional fields:

```json
{
  "source": "github" | "project",
  "project_path": "/path/to/project",
  "version": "7.2.0"
}
```

**`POST /api/system/upgrade`** — new optional fields:

```json
{
  "source": "github" | "project",
  "project_path": "/path/to/project",
  "force": true,
  "major_version": true,
  "version": "7.2.0"
}
```

Validation:
- `version` only valid with `source: "github"`
- `major_version` is a boolean, passed through to helper
- `force` is a boolean, bypasses preflight gate
- If `force` is false and no valid preflight file exists: return 400 with "Preflight check required. Run 'Check for Updates' first."

**`GET /api/system/upgrade/preflight`** — new endpoint:
- Requires `@admin_or_localhost` authentication (consistent with all other upgrade endpoints)
- Returns the current preflight report if it exists, or `{"preflight": null}` if no report exists (HTTP 200 in both cases — absence of a report is not an error)
- Used by the browser to read check results and determine if upgrade button should be enabled
- Response schema when report exists:

```json
{
  "preflight": {
    "timestamp": "2026-03-22T14:30:22Z",
    "source": "github",
    "current_version": "7.2.1",
    "target_version": "7.3.0",
    "is_major": false,
    "venv_rebuild_needed": false,
    "config_changes": [],
    "new_services": [],
    "files_changed": 42,
    "warnings": [],
    "stale": false
  }
}
```

- The `stale` field is computed server-side: `true` if timestamp is older than 30 minutes
- Browser uses `stale` to decide whether to re-enable or re-disable the upgrade button

**`GET /api/system/health`** — existing endpoint (already implemented in `utilities_system.py`):
- No authentication required (monitoring tools need unauthenticated access)
- Returns `{"status": "ok", "version": "X.Y.Z", "database": true/false}`
- Used by: Caddy maintenance page auto-reload, browser overlay recovery polling, and remote upgrade health checks
- No changes needed — this endpoint already exists and serves the purpose

### Helper changes (`upgrade-helper-process`)

New JSON field parsing:
- `force` → append `--force` to upgrade command
- `major_version` → append `--major-version` to upgrade command
- `version` → append `--version <value>` to upgrade command

Preflight file management:
- `do_upgrade_check()` writes preflight report to `/var/lib/audiobooks/.control/upgrade-preflight.json`
- `do_upgrade()` reads and validates preflight before proceeding (unless `force` is true)

### Web UI changes (`utilities.html`)

New elements in the Upgrade Application card:

**Options area (below source selection):**

```
[ ] Force upgrade (skip preflight check, reinstall even if versions match)
    ⚠ Bypasses all safety checks. Use only when you have a specific technical reason.

[ ] Major version upgrade (full venv rebuild + config migration + new services)
    Required when upgrading across major versions (e.g., 7.x → 8.x).
    Increases downtime to ~60 seconds.

Version: [________] (GitHub only, blank = latest)
```

**Button state logic:**
- "Start Upgrade" is disabled by default with tooltip "Run 'Check for Updates' first"
- After successful check: button enables
- If check detected major version: major version checkbox appears highlighted, must be checked to enable button
- If "Force" checkbox is checked: button enables immediately regardless of check status
- If source/version selection changes after check: button re-disables (check is stale)

**Force confirmation:**
When "Start Upgrade" is clicked with Force checked, the confirmation modal uses danger styling:

> **Force Upgrade — Safety Checks Bypassed**
>
> You are bypassing preflight verification. This skips safety checks designed to prevent failed upgrades, including:
> - Version comparison and compatibility check
> - Configuration impact analysis
> - Major version detection
>
> The installation backup will still be created.
>
> **Are you sure you want to proceed?**
>
> [Cancel] [Force Upgrade]

### Web UI changes (`utilities.js`)

- `checkUpgrade()`: on completion, store preflight data in a module-level variable and enable/disable the upgrade button based on results
- `startUpgrade()`: include `force`, `major_version`, `version` in the POST body
- New function `updateUpgradeButtonState()`: manages button enabled/disabled state based on preflight freshness, source changes, and force checkbox
- Staleness tracking: record check timestamp, invalidate after 10 minutes or when source/version inputs change

## Section 4: Caddy Maintenance Page

### Purpose

When all audiobook services are down during upgrade, external visitors hitting `library.thebosco.club` see a branded maintenance page instead of a generic Cloudflare 502 error. The page auto-reloads when services come back.

### Architecture

Caddy (port 8084) is the tunnel ingress target and stays running throughout audiobook upgrades — it's not part of `audiobook.target`. When the proxy upstream (8443) is unreachable, Caddy serves a static maintenance page.

### Files

**`/etc/caddy/conf.d/audiobooks.conf`** — Caddyfile snippet:

```caddyfile
:8084 {
    reverse_proxy https://localhost:8443 {
        transport http {
            tls_insecure_skip_verify
        }
    }

    handle_errors 502 503 {
        rewrite * /maintenance.html
        file_server {
            root /etc/caddy
        }
    }
}
```

**Scoped to 502/503 only.** A broad `handle_errors` (no status code filter) would serve the maintenance page for any upstream error including 404s and 500s from the running application, masking real application errors. 502 (Bad Gateway) and 503 (Service Unavailable) are the specific codes returned when the upstream proxy is unreachable — exactly the "services are down" condition we want to catch.

**`/etc/caddy/maintenance.html`** — Self-contained HTML page:

- Branded with Audiobook Library name and styling consistent with the app's dark theme
- Large, centered text: "Audiobook Library is being upgraded"
- Subtitle: "This page will reload automatically when the upgrade is complete."
- Animated spinner/pulse indicator
- Inline JS that polls the health endpoint (`/api/system/health`) every 3 seconds
- On successful health response: `location.reload()` (Caddy will proxy to the now-alive upstream)
- Meta refresh fallback (every 30 seconds) for browsers with JS disabled
- Fully self-contained: inline CSS, inline JS, no external dependencies

### Install/Upgrade Integration

**`install.sh`:**
- Creates `/etc/caddy/conf.d/audiobooks.conf` and `/etc/caddy/maintenance.html`
- Reloads Caddy: `systemctl reload caddy`
- Only if Caddy is installed (graceful skip otherwise)
- Also ensures `/var/lib/audiobooks/.control/` directory exists with correct ownership (`audiobooks:audiobooks`, mode 755) — this directory is used for the upgrade request/status/preflight files. `install.sh` already creates `$AUDIOBOOKS_VAR_DIR` but the `.control/` subdirectory may not exist on fresh installs.

**`upgrade.sh`:**
- Updates both files if they differ from the project source
- Reloads Caddy after update
- Part of the systemd templates sync stage (already handles files in `/etc/`)

**Project source location:**
- `caddy/audiobooks.conf` — Caddyfile snippet
- `caddy/maintenance.html` — maintenance page

## Section 5: Browser-Side Upgrade Resilience

### Upgrade Overlay

When "Start Upgrade" is confirmed, the JS creates a full-viewport overlay that replaces the entire page content. This overlay cannot be dismissed — the admin committed to the upgrade.

**Visual design:**
- Full-screen dark background matching the app's dark theme
- Centered content area, max-width 600px
- Large, high-contrast status text (minimum 24px, white on dark)
- Progress timeline showing all stages with checkmarks for completed, spinner for active, dimmed for pending

**Progress stages displayed:**

```
✓ Preflight re-validated
✓ Installation backed up
✓ Services stopped
● Upgrading files...          ← active stage, large spinner
○ Rebuilding venv             ← only shown for major version
○ Migrating config            ← only shown for major version
○ Starting services
○ Verifying
```

**Polling behavior:**
1. Normal polling (API is up): fetch `/api/system/upgrade/status` every 2 seconds, update progress stages
2. API goes down (fetch error): shift to "Services restarting — waiting for API..." message with spinner. NO error toast. This is expected.
3. Recovery polling: fetch `/api/system/health` every 2 seconds
4. API responds: read final status from `/api/system/upgrade/status`, show success/failure summary
5. Success: display new version number, "Upgrade Complete!" in large green text, auto-reload countdown (5 seconds) with "Reload Now" button
6. Failure: display error details in large red text, "Reload Application" button, suggestion to check server logs
7. Timeout (120 seconds with no API response): display warning that upgrade may have issues, manual reload button

**`beforeunload` warning:** While the overlay is active, `window.onbeforeunload` prevents accidental navigation away.

### Status File Durability

The helper writes the final upgrade result to `/var/lib/audiobooks/.control/upgrade-status` BEFORE restarting the API. The status file includes:

```json
{
  "running": false,
  "stage": "complete",
  "message": "Upgrade completed successfully",
  "success": true,
  "output": ["..."],
  "result": {
    "previous_version": "7.2.1",
    "new_version": "7.3.0",
    "duration_seconds": 45,
    "major_upgrade": false,
    "backup_path": "/opt/audiobooks.backup.20260322-143022",
    "warnings": []
  }
}
```

This ensures the browser can always read the result, even if it missed the pre-restart window.

## Section 6: Consistency Enforcement Rule

### New file: `.claude/rules/upgrade-consistency.md`

Documents the mandatory consistency requirement:

Any change to upgrade functionality in ANY of these files requires review and update of ALL of them:

| File | Role |
|---|---|
| `upgrade.sh` | CLI upgrade engine — flags, stages, behavior |
| `scripts/upgrade-helper-process` | Privileged bridge — translates API requests to upgrade.sh invocations |
| `library/backend/api_modular/utilities_system.py` | API endpoints — accepts web requests, validates, writes to helper |
| `library/web-v2/utilities.html` | Upgrade UI markup — options, buttons, overlay structure |
| `library/web-v2/js/utilities.js` | Upgrade UI logic — polling, state management, progress display |
| `install.sh` | First-time installation — must set up all infrastructure upgrade.sh expects |
| `caddy/audiobooks.conf` | Caddy config snippet — maintenance page routing |
| `caddy/maintenance.html` | External maintenance page — shown when services are down |

### Canonical service names

The canonical service names are defined in `systemd/*.service` files:

| Service | Unit Name |
|---|---|
| API server | `audiobook-api.service` |
| HTTPS proxy | `audiobook-proxy.service` |
| HTTP redirect | `audiobook-redirect.service` |
| Converter | `audiobook-converter.service` |
| Mover | `audiobook-mover.service` |
| Downloader | `audiobook-downloader.service` (+ `.timer`) |
| Shutdown saver | `audiobook-shutdown-saver.service` |
| Upgrade helper | `audiobook-upgrade-helper.service` (+ `.path`) |
| Scheduler | `audiobook-scheduler.service` |
| Target | `audiobook.target` |

All are `audiobook-*` (singular). Any code referencing `audiobooks-*` (plural) is a bug.

## Testing Plan

### Unit tests (dev machine)

- Test that `--skip-service-lifecycle` flag is parsed correctly
- Test that backup always runs (mock `create_backup`)
- Test preflight file generation and validation
- Test staleness detection (mock timestamps)
- Test force bypass of preflight gate

### Integration tests (test-audiobook-cachyos VM)

- Full upgrade from project: verify files sync, venv intact, services restart, version bumps
- Major version upgrade: verify venv rebuild, config migration, new service enablement
- Force upgrade: verify preflight bypass, backup still runs
- Web-triggered upgrade: verify browser polling handles API restart gracefully
- Caddy maintenance page: stop audiobook services, verify Caddy serves maintenance.html
- Service control from web UI: verify start/stop/restart actually affects real systemd units
- Preflight gate: verify upgrade refuses without prior check (unless force)
- Preflight staleness: verify upgrade refuses with stale check (> 30 minutes)

### Manual verification

- Admin performs full upgrade from web UI, never leaves browser
- External user hits site during upgrade, sees maintenance page, page auto-reloads when services return
- CLI user runs upgrade.sh directly — existing behavior preserved exactly
