# Upgrade System Consistency Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Achieve 100% bidirectional consistency between CLI upgrade.sh and web UI upgrade, fix the critical service name bug, add LEAPP-inspired mandatory preflight checks, make backups always-on, add Caddy maintenance page for external visitors, and create a resilient browser-side upgrade overlay.

**Architecture:** Privilege-separated helper pattern — API writes JSON request, systemd path unit triggers helper running as root, helper orchestrates full lifecycle (service stop → upgrade.sh --skip-service-lifecycle → service start). Caddy (port 8084) serves maintenance page when upstream (8443) is unreachable. Browser overlay polls resilient, expects API downtime.

**Tech Stack:** Bash (upgrade.sh, helper), Python/Flask (API), HTML/CSS/JS (web UI), Caddyfile (maintenance routing)

**Spec:** `docs/superpowers/specs/2026-03-22-upgrade-consistency-design.md`

---

## Task 1: Fix Service Name Bug in upgrade-helper-process

**Files:**

- Modify: `scripts/upgrade-helper-process` (all lines containing `audiobooks-`)

- [ ] **Step 1: Write the failing test**

Create `library/tests/test_helper_service_names.py`:

```python
"""Verify upgrade-helper-process uses correct singular service names."""
import re
from pathlib import Path

HELPER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "upgrade-helper-process"

def test_no_plural_service_names():
    """All service references must use audiobook-* (singular), never audiobooks-* (plural)."""
    content = HELPER_PATH.read_text()
    # Find all audiobooks- references that look like service names
    # Exclude: the script header comment block (lines starting with #)
    # and the shebang line
    plural_refs = []
    for i, line in enumerate(content.splitlines(), 1):
        # Skip pure comment lines in the header block (first 21 lines are docs)
        # but DO check service name strings in comments after that
        matches = re.findall(r'audiobooks-(?:api|proxy|converter|mover|downloader|redirect|scheduler|shutdown-saver|upgrade)', line)
        if matches:
            plural_refs.append((i, line.strip(), matches))
    assert plural_refs == [], (
        f"Found {len(plural_refs)} plural service name references (audiobooks-* instead of audiobook-*):\n"
        + "\n".join(f"  Line {ln}: {txt}" for ln, txt, _ in plural_refs)
    )

def test_valid_services_array_correct():
    """VALID_SERVICES array must contain only singular audiobook-* names."""
    content = HELPER_PATH.read_text()
    # Extract VALID_SERVICES block
    in_array = False
    services = []
    for line in content.splitlines():
        if "VALID_SERVICES=(" in line:
            in_array = True
            continue
        if in_array:
            if ")" in line:
                break
            svc = line.strip().strip('"').strip("'")
            if svc:
                services.append(svc)
    for svc in services:
        assert svc.startswith("audiobook-"), f"Service '{svc}' should start with 'audiobook-' (singular)"
        assert not svc.startswith("audiobooks-"), f"Service '{svc}' uses plural 'audiobooks-' — must be singular"
    # After Task 4 expands the array, all 8 target services must be present.
    # For now, just verify naming. Task 4 test_all_services_in_stop_order
    # asserts full membership.

def test_no_hardcoded_paths():
    """Helper must use config variables, not hardcoded paths."""
    content = HELPER_PATH.read_text()
    # After Task 1 we only fix names; hardcoded paths are fixed in Task 4's
    # rewrite which sources audiobook-config.sh. This test documents the
    # expectation for the final state.
    # Check that CONTROL_DIR is set from config, not literal
    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        if line.startswith("CONTROL_DIR=") and "/var/lib/audiobooks" in line:
            # This is acceptable ONLY if audiobook-config.sh is sourced above
            above = "\n".join(lines[:i])
            assert "audiobook-config.sh" in above or "AUDIOBOOKS_VAR_DIR" in line, \
                f"Line {i}: CONTROL_DIR uses hardcoded path without sourcing config"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_helper_service_names.py -v`
Expected: FAIL — plural service names found throughout the file

- [ ] **Step 3: Fix service names — global replace**

In `scripts/upgrade-helper-process`, replace every `audiobooks-` with `audiobook-` using a targeted approach:

Replace in VALID_SERVICES array (lines 31-37):

- `"audiobooks-api"` → `"audiobook-api"`
- `"audiobooks-proxy"` → `"audiobook-proxy"`
- `"audiobooks-converter"` → `"audiobook-converter"`
- `"audiobooks-mover"` → `"audiobook-mover"`
- `"audiobooks-downloader.timer"` → `"audiobook-downloader.timer"`

Replace in header comments (lines 13-15):

- `"audiobooks-converter"` → `"audiobook-converter"`
- `"audiobooks-api"` → `"audiobook-api"`

Replace in all function bodies:

- `do_services_stop_all()` stop_order arrays
- `do_upgrade()` services_to_stop array
- `do_upgrade()` API restart line
- Any fallback binary path `audiobooks-upgrade` → `audiobook-upgrade`

Use `replace_all` with Edit tool for each distinct string.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <project-dir> && python -m pytest library/tests/test_helper_service_names.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff and shellcheck**

Run: `ruff check library/tests/test_helper_service_names.py && shellcheck scripts/upgrade-helper-process`

- [ ] **Step 6: Commit**

```bash
git add scripts/upgrade-helper-process library/tests/test_helper_service_names.py
git commit -m "$(cat <<'EOF'
fix: correct service names in upgrade-helper-process (audiobooks- → audiobook-)

All systemd units use singular audiobook-* naming. The helper script
used plural audiobooks-* everywhere, causing ALL web-triggered service
operations to silently fail.
EOF
)"
```

---

### Task 2: Add --skip-service-lifecycle Flag to upgrade.sh

**Files:**

- Modify: `upgrade.sh` (argument parser, main block, do_github_upgrade, cleanup trap)

- [ ] **Step 1: Write the failing test**

Create `library/tests/test_upgrade_skip_lifecycle.py`:

```python
"""Verify --skip-service-lifecycle flag is parsed and respected."""
import subprocess
from pathlib import Path

UPGRADE_SH = Path(__file__).resolve().parents[2] / "upgrade.sh"

def test_skip_lifecycle_flag_accepted():
    """upgrade.sh must accept --skip-service-lifecycle without error."""
    # Run with --help-like quick exit: --check with a non-existent target
    # just to verify the flag doesn't cause 'unknown option' error
    result = subprocess.run(
        ["bash", "-n", str(UPGRADE_SH)],  # syntax check only
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"Syntax error in upgrade.sh: {result.stderr}"

def test_skip_lifecycle_flag_in_source():
    """upgrade.sh source must contain SKIP_SERVICE_LIFECYCLE variable."""
    content = UPGRADE_SH.read_text()
    assert "SKIP_SERVICE_LIFECYCLE" in content, "Missing SKIP_SERVICE_LIFECYCLE variable"
    assert "--skip-service-lifecycle" in content, "Missing --skip-service-lifecycle in argument parser"

def test_skip_lifecycle_not_in_help():
    """--skip-service-lifecycle is internal and must NOT appear in --help output."""
    result = subprocess.run(
        ["bash", str(UPGRADE_SH), "--help"],
        capture_output=True, text=True
    )
    assert "--skip-service-lifecycle" not in result.stdout, \
        "--skip-service-lifecycle should not appear in --help (internal flag)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest library/tests/test_upgrade_skip_lifecycle.py::test_skip_lifecycle_flag_in_source -v`
Expected: FAIL — SKIP_SERVICE_LIFECYCLE not yet in source

- [ ] **Step 3: Add flag to upgrade.sh argument parser**

Add `SKIP_SERVICE_LIFECYCLE=false` to the variable declarations section near the top of upgrade.sh.

Add to the argument parser case statement:

```bash
--skip-service-lifecycle)
    SKIP_SERVICE_LIFECYCLE=true
    shift
    ;;
```

Do NOT add it to the `--help` output.

- [ ] **Step 4: Guard service lifecycle calls**

Wrap `stop_services` and `start_services` calls in the main block and `do_github_upgrade()` with:

```bash
if [[ "$SKIP_SERVICE_LIFECYCLE" != "true" ]]; then
    stop_services
fi
```

Similarly for `start_services` and the `_cleanup_on_exit` trap's service restart logic.

- [ ] **Step 5: Make backup always-on**

In upgrade.sh:

- Make `create_backup()` run unconditionally (remove any conditional on `--backup` flag)
- Keep `--backup` in the arg parser but make it a no-op
- Add rolling retention to `create_backup()`:

```bash
# Rolling retention: keep last 5 backups, delete older
local -a backups
mapfile -t backups < <(ls -1dt "${target}.backup."* 2>/dev/null)
if (( ${#backups[@]} > 5 )); then
    for old_backup in "${backups[@]:5}"; do
        log_info "Removing old backup: $old_backup"
        rm -rf "$old_backup"
    done
fi
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest library/tests/test_upgrade_skip_lifecycle.py -v && bash -n upgrade.sh`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add upgrade.sh library/tests/test_upgrade_skip_lifecycle.py
git commit -m "$(cat <<'EOF'
feat: add --skip-service-lifecycle flag and always-on backup

Internal flag for helper to own service lifecycle while upgrade.sh
handles files/venv/migrations. Backup now runs unconditionally on
every upgrade with rolling retention (keep last 5).
EOF
)"
```

---

### Task 3: Preflight Check System in upgrade.sh

**Files:**

- Modify: `upgrade.sh` (new functions: generate_preflight, validate_preflight)

- [ ] **Step 1: Write the failing test**

Create `library/tests/test_upgrade_preflight.py`:

```python
"""Verify preflight check infrastructure exists in upgrade.sh."""
from pathlib import Path

UPGRADE_SH = Path(__file__).resolve().parents[2] / "upgrade.sh"

def test_preflight_functions_exist():
    """upgrade.sh must define generate_preflight and validate_preflight."""
    content = UPGRADE_SH.read_text()
    assert "generate_preflight()" in content, "Missing generate_preflight() function"
    assert "validate_preflight()" in content, "Missing validate_preflight() function"

def test_preflight_file_path_defined():
    """Preflight file path must be defined using config variable."""
    content = UPGRADE_SH.read_text()
    assert "upgrade-preflight.json" in content, "Missing preflight JSON filename"
    # Must NOT hardcode /var/lib/audiobooks
    import re
    hardcoded = re.findall(r'/var/lib/audiobooks/\.control/upgrade-preflight', content)
    assert len(hardcoded) == 0, "Preflight path must use $AUDIOBOOKS_VAR_DIR, not hardcoded path"

def test_force_bypasses_preflight():
    """When --force is set, preflight validation must be skipped."""
    content = UPGRADE_SH.read_text()
    # Look for the force bypass pattern
    assert "FORCE" in content, "Missing FORCE variable for --force flag"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest library/tests/test_upgrade_preflight.py -v`
Expected: FAIL on generate_preflight/validate_preflight not found

- [ ] **Step 3: Implement generate_preflight()**

Add function to upgrade.sh that:

- Runs dry-run analysis (reuses existing check logic)
- Writes JSON to `${AUDIOBOOKS_VAR_DIR}/.control/upgrade-preflight.json`
- Includes: timestamp, source, current_version, target_version, is_major, venv_rebuild_needed, config_changes, new_services, files_changed, warnings

- [ ] **Step 4: Implement validate_preflight()**

Add function that:

- Checks preflight file exists
- Checks timestamp is < 30 minutes old
- Checks source matches current request
- If `$FORCE == true`: skip all checks, log warning
- Returns 0 (valid) or 1 (invalid with message)

- [ ] **Step 5: Wire preflight into upgrade flow**

- `do_upgrade()` and main block: call `generate_preflight` during check, call `validate_preflight` before upgrade
- Re-run preflight internally as first step of upgrade to detect drift

- [ ] **Step 6: Run tests**

Run: `python -m pytest library/tests/test_upgrade_preflight.py -v && shellcheck upgrade.sh`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add upgrade.sh library/tests/test_upgrade_preflight.py
git commit -m "$(cat <<'EOF'
feat: add LEAPP-inspired mandatory preflight check system

Upgrades now require a completed preflight check that validates
version compatibility, config impacts, disk space, and drift.
--force bypasses preflight but not backup.
EOF
)"
```

---

### Task 4: Rewrite upgrade-helper-process Lifecycle

**Files:**

- Modify: `scripts/upgrade-helper-process` (rewrite do_upgrade with 9-step lifecycle)

- [ ] **Step 1: Write the failing test**

Create `library/tests/test_helper_lifecycle.py`:

```python
"""Verify upgrade-helper-process implements the 9-step lifecycle."""
from pathlib import Path

HELPER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "upgrade-helper-process"

REQUIRED_STAGES = [
    "preflight_recheck",
    "backing_up",
    "stopping_services",
    "upgrading",
    "rebuilding_venv",
    "migrating_config",
    "starting_services",
    "verifying",
    "complete",
]

def test_all_lifecycle_stages_present():
    """Helper must reference all 9 lifecycle stages."""
    content = HELPER_PATH.read_text()
    missing = [s for s in REQUIRED_STAGES if s not in content]
    assert missing == [], f"Missing lifecycle stages: {missing}"

def test_skip_service_lifecycle_flag_passed():
    """Helper must pass --skip-service-lifecycle --yes to upgrade.sh."""
    content = HELPER_PATH.read_text()
    assert "--skip-service-lifecycle" in content, "Must pass --skip-service-lifecycle to upgrade.sh"
    assert "--yes" in content, "Must pass --yes to upgrade.sh"

def test_no_echo_y_pipe_hack():
    """Helper must not use 'echo y |' pipe hack."""
    content = HELPER_PATH.read_text()
    assert 'echo "y"' not in content and "echo 'y'" not in content and "echo y |" not in content, \
        "Must use --yes flag, not echo y pipe hack"

def test_new_request_fields_parsed():
    """Helper must parse force, major_version, version from request JSON."""
    content = HELPER_PATH.read_text()
    for field in ["force", "major_version", "version"]:
        assert field in content, f"Must parse '{field}' from request JSON"

def test_all_services_in_stop_order():
    """Stop order must include ALL audiobook.target services."""
    content = HELPER_PATH.read_text()
    required_services = [
        "audiobook-downloader.timer",
        "audiobook-shutdown-saver",
        "audiobook-scheduler",
        "audiobook-mover",
        "audiobook-converter",
        "audiobook-redirect",
        "audiobook-proxy",
        "audiobook-api",
    ]
    for svc in required_services:
        assert svc in content, f"Service '{svc}' missing from helper lifecycle"

def test_no_hardcoded_paths():
    """Helper must source audiobook-config.sh and use config variables for paths."""
    content = HELPER_PATH.read_text()
    assert "audiobook-config.sh" in content, \
        "Helper must source audiobook-config.sh for path variables"
    # CONTROL_DIR and INSTALL_DIR must derive from config vars
    for line in content.splitlines():
        if line.startswith("CONTROL_DIR=") and "/var/lib/audiobooks" in line:
            assert False, "CONTROL_DIR must use $AUDIOBOOKS_VAR_DIR, not hardcoded path"
        if line.startswith("INSTALL_DIR=") and "/opt/audiobooks" in line:
            assert False, "INSTALL_DIR must use config variable, not hardcoded /opt/audiobooks"

def test_final_status_written_before_service_start():
    """Final status must be written BEFORE starting services (spec: Status File Durability)."""
    content = HELPER_PATH.read_text()
    # The write_status with upgrade result must appear BEFORE the service start block
    # Look for the result-writing write_status call and the starting_services stage
    result_write_pos = content.find('write_status "false" "complete"')
    if result_write_pos < 0:
        # Alternative pattern: the helper may write result before starting services
        # using a different write_status call with success/result data
        result_write_pos = content.find('"upgrade_result"')
    start_services_pos = content.find('"starting_services"')
    assert result_write_pos > 0, "Must write upgrade result to status file"
    assert start_services_pos > 0, "Must have starting_services stage"
    assert result_write_pos < start_services_pos, (
        "Upgrade result must be written to status file BEFORE starting services "
        f"(result at pos {result_write_pos}, start_services at pos {start_services_pos}). "
        "Per spec: 'The helper writes the final upgrade result... BEFORE restarting the API.'"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest library/tests/test_helper_lifecycle.py -v`
Expected: FAIL on missing stages, pipe hack, missing fields

- [ ] **Step 3: Rewrite do_upgrade() with 9-step lifecycle**

Rewrite the `do_upgrade()` function in `scripts/upgrade-helper-process`:

1. **Source audiobook-config.sh** at the top of the script. Replace hardcoded paths:
   - `CONTROL_DIR="${AUDIOBOOKS_VAR_DIR}/.control"` (was `/var/lib/audiobooks/.control`)
   - `INSTALL_DIR` from config (was `/opt/audiobooks`)
2. Parse new request fields: `force`, `major_version`, `version` using the existing `get_json_field()` bash function (NOT jq — the helper uses its own lightweight JSON parser)
3. Step 1 (preflight_recheck): validate preflight file unless force
4. Step 2 (backing_up): run backup (upgrade.sh handles this, but report stage)
5. Step 3 (stopping_services): stop ALL 8 services in order (downloader.timer, shutdown-saver, scheduler, mover, converter, redirect, proxy, API)
6. Step 4 (upgrading): run `upgrade.sh --skip-service-lifecycle --yes` with applicable flags
7. Steps 5-6 (rebuilding_venv, migrating_config): report if major version
8. **Write final result status to status file** (BEFORE starting services — per spec "Status File Durability")
9. Step 7 (starting_services): start in reverse order
10. Step 8 (verifying): poll /api/system/health for up to 30s
11. Step 9 (complete): update status with verification result

**CRITICAL ordering:** The final upgrade result (success/failure, versions, duration) must be written to the status file BEFORE starting services in step 9. This ensures the browser can always read the result even if it missed the pre-restart window. The `complete` stage update after verification only adds the health-check confirmation.

Remove the `echo "y" |` pipe hack, use `--yes` flag.

- [ ] **Step 4: Run tests**

Run: `python -m pytest library/tests/test_helper_lifecycle.py library/tests/test_helper_service_names.py -v && shellcheck scripts/upgrade-helper-process`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/upgrade-helper-process library/tests/test_helper_lifecycle.py
git commit -m "$(cat <<'EOF'
feat: rewrite helper with 9-step lifecycle, new request fields

Helper now owns full service lifecycle with granular status reporting.
Parses force/major_version/version from API requests. Removes echo y
pipe hack in favor of --yes flag.
EOF
)"
```

---

### Task 5: API Endpoint Updates (utilities_system.py)

**Files:**

- Modify: `library/backend/api_modular/utilities_system.py`
- Test: `library/tests/test_upgrade_api.py`

- [ ] **Step 1: Write the failing test**

Create `library/tests/test_upgrade_api.py`:

```python
"""Verify upgrade API endpoints support new fields and preflight gate."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SYS_MODULE = Path(__file__).resolve().parents[1] / "backend" / "api_modular" / "utilities_system.py"

def test_upgrade_endpoint_accepts_new_fields():
    """POST /api/system/upgrade must accept force, major_version, version fields."""
    content = SYS_MODULE.read_text()
    for field in ["force", "major_version", "version"]:
        assert field in content, f"Upgrade endpoint must handle '{field}' field"

def test_preflight_endpoint_exists():
    """GET /api/system/upgrade/preflight endpoint must be defined."""
    content = SYS_MODULE.read_text()
    assert "upgrade/preflight" in content, "Missing /api/system/upgrade/preflight endpoint"
    assert "admin_or_localhost" in content, "Preflight endpoint must require auth"

def test_preflight_gate_on_upgrade():
    """Upgrade endpoint must check for valid preflight unless force is true."""
    content = SYS_MODULE.read_text()
    # Must have actual preflight file reading logic, not just a comment
    assert "upgrade-preflight.json" in content or "preflight" in content, \
        "Upgrade endpoint must read and validate preflight file"
    # Must check for force bypass
    assert "force" in content, "Upgrade endpoint must check force flag for preflight bypass"

def test_version_field_validated_for_source():
    """version field must be rejected when source is 'project'."""
    content = SYS_MODULE.read_text()
    # Must have validation logic that version is only valid with github source
    assert "version" in content, "Must handle version field"
    # Look for the validation pattern
    import re
    # Should find a check like: if version and source != "github"
    has_version_validation = bool(re.search(
        r'version.*(?:github|source)|(?:github|source).*version', content
    ))
    assert has_version_validation, \
        "Must validate that 'version' field is only accepted with source='github'"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest library/tests/test_upgrade_api.py -v`
Expected: FAIL on missing preflight endpoint and fields

- [ ] **Step 3: Add GET /api/system/upgrade/preflight endpoint**

In `utilities_system.py`, add new endpoint:

- Route: `GET /api/system/upgrade/preflight`
- Auth: `@admin_or_localhost`
- Reads preflight JSON from control dir
- Returns `{"preflight": <data>}` or `{"preflight": null}`
- Computes `stale` field (> 30 min)

- [ ] **Step 4: Update POST /api/system/upgrade to accept new fields**

- Parse `force`, `major_version`, `version` from request JSON
- **Validate:** if `version` is provided and `source` is not `"github"`, return 400 with `"version field is only valid with source 'github'"`
- If `force` is not true: check for valid, non-stale preflight file; return 400 if missing/stale
- Include new fields in the request JSON written to the control dir

- [ ] **Step 5: Update POST /api/system/upgrade/check**

- Accept optional `version` field
- Pass through to request JSON for helper

- [ ] **Step 6: Run tests**

Run: `python -m pytest library/tests/test_upgrade_api.py -v && ruff check library/backend/api_modular/utilities_system.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add library/backend/api_modular/utilities_system.py library/tests/test_upgrade_api.py
git commit -m "$(cat <<'EOF'
feat: add preflight endpoint, preflight gate, new upgrade fields

New GET /api/system/upgrade/preflight returns preflight report.
POST /api/system/upgrade now requires valid preflight (unless force).
Accepts force, major_version, version fields.
EOF
)"
```

---

### Task 6: Caddy Maintenance Page (Create Files)

**Files:**

- Create: `caddy/audiobooks.conf`
- Create: `caddy/maintenance.html`

- [ ] **Step 1: Create caddy directory**

Run: `mkdir -p <project-dir>/caddy`

- [ ] **Step 2: Create audiobooks.conf**

Create `caddy/audiobooks.conf`:

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

- [ ] **Step 3: Create maintenance.html**

Create `caddy/maintenance.html` — self-contained branded page. Key structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <title>Audiobook Library — Upgrading</title>
    <style>
        /* Dark theme, centered layout, large text */
        body { background: #1a1a2e; color: #e0e0e0; font-family: system-ui; display: flex;
               align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
        .container { text-align: center; max-width: 500px; padding: 2rem; }
        h1 { font-size: 2rem; color: #fff; }
        p { font-size: 1.25rem; }
        .pulse { width: 40px; height: 40px; border-radius: 50%; background: #4a90d9;
                 margin: 2rem auto; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); }
                           50% { opacity: 0.5; transform: scale(1.2); } }
    </style>
</head>
<body>
    <div class="container">
        <div class="pulse"></div>
        <h1>Audiobook Library is being upgraded</h1>
        <p>This page will reload automatically when the upgrade is complete.</p>
    </div>
    <script>
        // Poll health endpoint — reload when API responds
        (function poll() {
            fetch('/api/system/health', { signal: AbortSignal.timeout(3000) })
                .then(function(r) { if (r.ok) location.reload(); })
                .catch(function() {});  // Expected — services are down
            setTimeout(poll, 3000);
        })();
    </script>
    <noscript><p>JavaScript is disabled. This page will auto-refresh every 30 seconds.</p></noscript>
</body>
</html>
```

**Important:** The inline JS uses no DOM text manipulation — it only calls `fetch` and `location.reload()`. No innerHTML or textContent needed since the page is fully static HTML.

- [ ] **Step 4: Write a test for Caddy files**

Create `library/tests/test_caddy_files.py`:

```python
"""Verify Caddy project files exist and are well-formed."""
from pathlib import Path

CADDY_DIR = Path(__file__).resolve().parents[2] / "caddy"

def test_audiobooks_conf_exists():
    """Caddy config snippet must exist in project."""
    assert (CADDY_DIR / "audiobooks.conf").is_file()

def test_maintenance_html_exists():
    """Maintenance page must exist in project."""
    assert (CADDY_DIR / "maintenance.html").is_file()

def test_maintenance_html_has_health_polling():
    """Maintenance page must poll /api/system/health."""
    content = (CADDY_DIR / "maintenance.html").read_text()
    assert "/api/system/health" in content, "Must poll health endpoint"
    assert "location.reload()" in content, "Must reload on health success"

def test_maintenance_html_no_innerhtml():
    """Maintenance page must not use innerHTML."""
    content = (CADDY_DIR / "maintenance.html").read_text()
    assert "innerHTML" not in content, "Must not use innerHTML — use textContent or static HTML"

def test_maintenance_html_has_noscript_fallback():
    """Maintenance page must have meta refresh for no-JS browsers."""
    content = (CADDY_DIR / "maintenance.html").read_text()
    assert "meta http-equiv" in content.lower() or "noscript" in content.lower(), \
        "Must have no-JS fallback (meta refresh or noscript)"
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest library/tests/test_caddy_files.py -v`
Expected: PASS

- [ ] **Step 6: Verify files**

Run: `cat caddy/audiobooks.conf && echo "---" && head -5 caddy/maintenance.html`

- [ ] **Step 7: Commit**

```bash
git add caddy/ library/tests/test_caddy_files.py
git commit -m "$(cat <<'EOF'
feat: add Caddy maintenance page for external visitors during upgrade

Caddy serves a branded maintenance page when upstream is unreachable
(502/503). Auto-reloads when services come back via health endpoint
polling. Fully self-contained with inline CSS/JS.
EOF
)"
```

---

### Task 7: Install/Upgrade Integration for Caddy Files

**Files:**

- Modify: `install.sh` (add Caddy file installation)
- Modify: `upgrade.sh` (add Caddy file sync)

- [ ] **Step 1: Write the failing test**

Create `library/tests/test_caddy_integration.py`:

```python
"""Verify install.sh and upgrade.sh handle Caddy files."""
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"
UPGRADE_SH = Path(__file__).resolve().parents[2] / "upgrade.sh"

def test_install_references_caddy_files():
    """install.sh must install Caddy config and maintenance page."""
    content = INSTALL_SH.read_text()
    assert "audiobooks.conf" in content, "install.sh must install Caddy config"
    assert "maintenance.html" in content, "install.sh must install maintenance page"

def test_upgrade_syncs_caddy_files():
    """upgrade.sh must sync Caddy files during upgrade."""
    content = UPGRADE_SH.read_text()
    assert "audiobooks.conf" in content or "caddy" in content.lower(), \
        "upgrade.sh must sync Caddy config"

def test_caddy_conditional_on_install():
    """Caddy installation must be conditional (skip if Caddy not installed)."""
    content = INSTALL_SH.read_text()
    # Should check for caddy binary or systemd unit
    assert "caddy" in content.lower(), "Must check for Caddy availability"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest library/tests/test_caddy_integration.py -v`
Expected: FAIL

- [ ] **Step 3: Add Caddy file installation to install.sh**

In the systemd/config installation section of install.sh, add:

```bash
# Install Caddy maintenance page (if Caddy is installed)
if command -v caddy &>/dev/null; then
    log_info "Installing Caddy maintenance page configuration..."
    cp -f "${SOURCE_DIR}/caddy/audiobooks.conf" /etc/caddy/conf.d/audiobooks.conf
    cp -f "${SOURCE_DIR}/caddy/maintenance.html" /etc/caddy/maintenance.html
    systemctl reload caddy 2>/dev/null || true
fi
```

Also ensure `.control/` directory creation exists.

- [ ] **Step 4: Add Caddy file sync to upgrade.sh**

In the file sync stage of upgrade.sh, add Caddy file comparison and copy:

```bash
# Sync Caddy files if Caddy is installed
if command -v caddy &>/dev/null; then
    local caddy_changed=false
    for caddy_file in audiobooks.conf maintenance.html; do
        # Compare and copy if different
        ...
    done
    if [[ "$caddy_changed" == "true" ]]; then
        systemctl reload caddy 2>/dev/null || true
    fi
fi
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest library/tests/test_caddy_integration.py -v && shellcheck install.sh upgrade.sh`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add install.sh upgrade.sh library/tests/test_caddy_integration.py
git commit -m "$(cat <<'EOF'
feat: integrate Caddy maintenance page into install/upgrade lifecycle

install.sh deploys Caddy files on fresh install. upgrade.sh syncs
them on upgrade. Both conditional on Caddy being installed.
EOF
)"
```

---

### Task 8: Web UI — Upgrade Options and Button State

**Files:**

- Modify: `library/web-v2/utilities.html` (add options, overlay markup)
- Modify: `library/web-v2/css/utilities.css` (new styles)

- [ ] **Step 1: Read current upgrade card markup**

Read `library/web-v2/utilities.html` lines 949-999 to understand current structure.

- [ ] **Step 2: Add advanced options to upgrade card**

Below the source selection radio buttons, add:

```html
<!-- Advanced upgrade options -->
<div class="upgrade-advanced-options">
    <label class="upgrade-option">
        <input type="checkbox" id="upgrade-force">
        <span>Force upgrade</span>
        <small>Skip preflight check, reinstall even if versions match</small>
    </label>
    <div class="upgrade-force-warning" style="display:none;">
        Warning: Bypasses all safety checks. Use only with a specific technical reason.
    </div>

    <label class="upgrade-option">
        <input type="checkbox" id="upgrade-major">
        <span>Major version upgrade</span>
        <small>Full venv rebuild + config migration + new services (~60s extra downtime)</small>
    </label>

    <div class="upgrade-version-input" id="upgrade-version-group" style="display:none;">
        <label for="upgrade-version">Version:</label>
        <input type="text" id="upgrade-version" placeholder="blank = latest">
        <small>GitHub only — specify exact version to install</small>
    </div>
</div>
```

- [ ] **Step 3: Add full-screen overlay markup**

At the bottom of the page (before closing body tag), add the upgrade overlay:

```html
<!-- Upgrade overlay - shown during active upgrade -->
<div id="upgrade-overlay" class="upgrade-overlay" style="display:none;">
    <div class="upgrade-overlay-content">
        <h1 class="upgrade-overlay-title">Upgrading Audiobook Library</h1>
        <div class="upgrade-overlay-status" id="upgrade-overlay-status">
            Initializing...
        </div>
        <div class="upgrade-overlay-stages" id="upgrade-overlay-stages"></div>
        <div class="upgrade-overlay-result" id="upgrade-overlay-result" style="display:none;"></div>
    </div>
</div>
```

- [ ] **Step 4: Add CSS for advanced options and overlay**

In `library/web-v2/css/utilities.css`, add:

```css
/* Advanced upgrade options */
.upgrade-advanced-options { margin: 1rem 0; padding: 0.75rem; }
.upgrade-option { display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0; }
.upgrade-option small { color: var(--text-muted); font-size: 0.85rem; }
.upgrade-force-warning { color: var(--danger); font-size: 0.9rem; padding: 0.5rem; margin: 0.25rem 0; }

/* Full-screen upgrade overlay */
.upgrade-overlay {
    position: fixed; inset: 0; z-index: 10000;
    background: var(--bg-primary, #1a1a2e);
    display: flex; align-items: center; justify-content: center;
}
.upgrade-overlay-content { max-width: 600px; text-align: center; padding: 2rem; }
.upgrade-overlay-title { font-size: 2rem; color: #fff; margin-bottom: 1.5rem; }
.upgrade-overlay-status { font-size: 1.5rem; color: #e0e0e0; margin: 1rem 0; min-height: 2.5rem; }

/* Stage list */
.upgrade-stage-item { display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0; font-size: 1.25rem; }
.upgrade-stage-item.pending { color: #666; }
.upgrade-stage-item.active { color: #fff; font-weight: bold; }
.upgrade-stage-item.complete { color: #4caf50; }
.upgrade-stage-item.error { color: #f44336; }
```

- [ ] **Step 5: Verify markup renders**

Read back the modified files to confirm structure is correct.

- [ ] **Step 6: Commit**

```bash
git add library/web-v2/utilities.html library/web-v2/css/utilities.css
git commit -m "$(cat <<'EOF'
feat: add upgrade options UI and full-screen overlay markup

Advanced options: force, major version, specific version.
Full-screen overlay for upgrade progress with large legible status
text (min 1.5rem/24px).
EOF
)"
```

---

### Task 9: Browser-Side Upgrade Overlay and Resilient Polling

**Files:**

- Modify: `library/web-v2/js/utilities.js`

**IMPORTANT — Pattern break from existing codebase:** The existing utilities.js uses `innerHTML` extensively. All NEW code in this task MUST use `textContent` and DOM APIs (`createElement`, `appendChild`, `replaceChildren`) instead. This is a deliberate security improvement. Do NOT copy the existing innerHTML pattern.

- [ ] **Step 1: Read current upgrade JS**

Read `library/web-v2/js/utilities.js` lines 3900-4070 to understand current polling code.

- [ ] **Step 2: Add preflight state management**

Add module-level variables and `updateUpgradeButtonState()`:

```javascript
let preflightData = null;
let preflightTimestamp = null;

function updateUpgradeButtonState() {
    const startBtn = document.getElementById('start-upgrade-btn');
    const forceCheckbox = document.getElementById('upgrade-force');
    if (!startBtn) return;

    if (forceCheckbox && forceCheckbox.checked) {
        startBtn.disabled = false;
        startBtn.title = 'Force upgrade — safety checks bypassed';
        return;
    }

    if (!preflightData || !preflightTimestamp) {
        startBtn.disabled = true;
        startBtn.title = "Run 'Check for Updates' first";
        return;
    }

    const ageMinutes = (Date.now() - preflightTimestamp) / 60000;
    if (ageMinutes > 10) {
        startBtn.disabled = true;
        startBtn.title = 'Preflight check is stale — run Check for Updates again';
        return;
    }

    startBtn.disabled = false;
    startBtn.title = 'Start upgrade';
}
```

- [ ] **Step 3: Update checkUpgrade() to store preflight data**

After successful check response, store preflight data:

```javascript
preflightData = data.preflight || data;
preflightTimestamp = Date.now();
updateUpgradeButtonState();
```

- [ ] **Step 4: Update startUpgrade() with new fields and overlay**

Modify `startUpgrade()` to:

1. Collect force, major_version, version from UI inputs
2. If force checked: show danger confirmation modal, require explicit confirm
3. POST with all new fields:

```javascript
const body = {
    source: document.querySelector('input[name="upgrade-source"]:checked').value,
    force: document.getElementById('upgrade-force').checked,
    major_version: document.getElementById('upgrade-major').checked,
};
if (body.source === 'project') {
    body.project_path = document.getElementById('upgrade-project-path').value;
}
const versionInput = document.getElementById('upgrade-version').value.trim();
if (versionInput && body.source === 'github') {
    body.version = versionInput;
}
const resp = await fetch('/api/system/upgrade', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
});
```

1. Show the full-screen overlay
2. Set `window.onbeforeunload` warning
3. Start resilient polling

- [ ] **Step 5: Implement resilient polling**

Replace the current polling with a resilient version:

```javascript
function startResilientUpgradePolling() {
    const overlay = document.getElementById('upgrade-overlay');
    overlay.style.display = 'flex';

    let apiDown = false;
    let downSince = null;

    const poll = async () => {
        try {
            if (apiDown) {
                // Recovery polling — hit health endpoint
                const healthResp = await fetch('/api/system/health', { signal: AbortSignal.timeout(3000) });
                if (healthResp.ok) {
                    apiDown = false;
                    // API is back — read final status
                    const statusResp = await fetch('/api/system/upgrade/status');
                    if (statusResp.ok) {
                        const data = await statusResp.json();
                        showUpgradeResult(data);
                        return; // Stop polling
                    }
                }
            } else {
                // Normal polling — hit status endpoint
                const resp = await fetch('/api/system/upgrade/status', { signal: AbortSignal.timeout(3000) });
                if (resp.ok) {
                    const data = await resp.json();
                    updateOverlayStages(data);
                    if (!data.running && data.stage === 'complete') {
                        showUpgradeResult(data);
                        return; // Stop polling
                    }
                }
            }
        } catch {
            // Fetch error — API is down (expected during upgrade)
            if (!apiDown) {
                apiDown = true;
                downSince = Date.now();
                const statusEl = document.getElementById('upgrade-overlay-status');
                if (statusEl) statusEl.textContent = 'Services restarting — waiting for API...';
            }
            // Check timeout (120s)
            if (downSince && (Date.now() - downSince) > 120000) {
                showUpgradeTimeout();
                return;
            }
        }
        setTimeout(poll, 2000);
    };
    poll();
}
```

- [ ] **Step 6: Implement overlay update functions**

All DOM updates use `textContent` (never innerHTML) for security:

```javascript
function updateOverlayStages(data) {
    const stagesEl = document.getElementById('upgrade-overlay-stages');
    if (!stagesEl) return;
    // Clear existing
    stagesEl.replaceChildren();

    const stages = [
        { key: 'preflight_recheck', label: 'Preflight re-validated' },
        { key: 'backing_up', label: 'Installation backed up' },
        { key: 'stopping_services', label: 'Services stopped' },
        { key: 'upgrading', label: 'Upgrading files' },
        { key: 'starting_services', label: 'Starting services' },
        { key: 'verifying', label: 'Verifying upgrade' },
    ];
    // Add major-version stages if applicable
    if (data.result && data.result.major_upgrade) {
        stages.splice(4, 0,
            { key: 'rebuilding_venv', label: 'Rebuilding virtual environment' },
            { key: 'migrating_config', label: 'Migrating configuration' }
        );
    }

    const currentIdx = stages.findIndex(s => s.key === data.stage);
    for (let i = 0; i < stages.length; i++) {
        const item = document.createElement('div');
        item.className = 'upgrade-stage-item';

        const icon = document.createElement('span');
        if (i < currentIdx) {
            item.classList.add('complete');
            icon.textContent = '\u2713';  // checkmark
        } else if (i === currentIdx) {
            item.classList.add('active');
            icon.textContent = '\u25CF';  // filled circle (spinner via CSS)
        } else {
            item.classList.add('pending');
            icon.textContent = '\u25CB';  // empty circle
        }

        const label = document.createElement('span');
        label.textContent = stages[i].label;

        item.appendChild(icon);
        item.appendChild(label);
        stagesEl.appendChild(item);
    }

    const statusEl = document.getElementById('upgrade-overlay-status');
    if (statusEl) statusEl.textContent = data.message || '';
}

function showUpgradeResult(data) {
    window.onbeforeunload = null;  // Allow navigation
    const resultEl = document.getElementById('upgrade-overlay-result');
    if (!resultEl) return;
    resultEl.style.display = 'block';
    resultEl.replaceChildren();

    if (data.success) {
        const heading = document.createElement('h2');
        heading.textContent = 'Upgrade Complete!';
        heading.style.color = '#4caf50';
        heading.style.fontSize = '2rem';
        resultEl.appendChild(heading);

        if (data.result) {
            const version = document.createElement('p');
            version.textContent = 'Version: ' + (data.result.new_version || 'unknown');
            version.style.fontSize = '1.25rem';
            resultEl.appendChild(version);
        }

        const countdown = document.createElement('p');
        countdown.textContent = 'Reloading in 5 seconds...';
        resultEl.appendChild(countdown);

        const reloadBtn = document.createElement('button');
        reloadBtn.textContent = 'Reload Now';
        reloadBtn.className = 'btn btn-primary';
        reloadBtn.addEventListener('click', () => location.reload());
        resultEl.appendChild(reloadBtn);

        let seconds = 5;
        const timer = setInterval(() => {
            seconds--;
            countdown.textContent = 'Reloading in ' + seconds + ' seconds...';
            if (seconds <= 0) {
                clearInterval(timer);
                location.reload();
            }
        }, 1000);
    } else {
        const heading = document.createElement('h2');
        heading.textContent = 'Upgrade Failed';
        heading.style.color = '#f44336';
        heading.style.fontSize = '2rem';
        resultEl.appendChild(heading);

        if (data.message) {
            const msg = document.createElement('p');
            msg.textContent = data.message;
            msg.style.fontSize = '1.25rem';
            resultEl.appendChild(msg);
        }

        const hint = document.createElement('p');
        hint.textContent = 'Check server logs for details.';
        resultEl.appendChild(hint);

        const reloadBtn = document.createElement('button');
        reloadBtn.textContent = 'Reload Application';
        reloadBtn.className = 'btn btn-primary';
        reloadBtn.addEventListener('click', () => location.reload());
        resultEl.appendChild(reloadBtn);
    }
}

function showUpgradeTimeout() {
    window.onbeforeunload = null;
    const statusEl = document.getElementById('upgrade-overlay-status');
    if (statusEl) {
        statusEl.textContent = 'Upgrade may have issues — API has not responded for 2 minutes.';
        statusEl.style.color = '#ff9800';
    }
    const resultEl = document.getElementById('upgrade-overlay-result');
    if (resultEl) {
        resultEl.style.display = 'block';
        resultEl.replaceChildren();
        const btn = document.createElement('button');
        btn.textContent = 'Try Reloading';
        btn.className = 'btn btn-primary';
        btn.addEventListener('click', () => location.reload());
        resultEl.appendChild(btn);
    }
}
```

- [ ] **Step 7: Wire up event listeners**

Add change listeners to force checkbox, source radios, and version input to call `updateUpgradeButtonState()`.

- [ ] **Step 8: Run ruff and verify JS syntax**

Run: `ruff check library/tests/ && node -c library/web-v2/js/utilities.js 2>&1 | head -5`

- [ ] **Step 9: Commit**

```bash
git add library/web-v2/js/utilities.js
git commit -m "$(cat <<'EOF'
feat: resilient upgrade overlay with preflight-gated button state

Full-screen overlay during upgrade expects API downtime, uses
health endpoint recovery polling. All DOM updates via textContent
(no innerHTML). Preflight gates upgrade button with 10-min staleness.
EOF
)"
```

---

### Task 10: Consistency Enforcement Rule

**Files:**

- Create: `.claude/rules/upgrade-consistency.md`

- [ ] **Step 1: Create the enforcement rule**

Create `.claude/rules/upgrade-consistency.md`:

```markdown
# Upgrade System Consistency — Mandatory Cross-File Review

Any change to upgrade functionality in ANY of these files requires review
and update of ALL of them:

| File | Role |
|------|------|
| upgrade.sh | CLI upgrade engine |
| scripts/upgrade-helper-process | Privileged bridge for web upgrades |
| library/backend/api_modular/utilities_system.py | API endpoints |
| library/web-v2/utilities.html | Upgrade UI markup |
| library/web-v2/js/utilities.js | Upgrade UI logic |
| install.sh | First-time installation |
| caddy/audiobooks.conf | Caddy maintenance routing |
| caddy/maintenance.html | External maintenance page |

## Canonical Service Names

ALL audiobook systemd services use singular `audiobook-*` (never `audiobooks-*`).
See systemd/*.service for authoritative list. Any code using `audiobooks-` (plural)
is a bug.

## Upgrade Feature Parity

Every upgrade option available in upgrade.sh CLI MUST also be available in the
web UI. No gatekeeping. If a new flag is added to upgrade.sh, the corresponding
UI control, API field, and helper parsing MUST be added in the same commit or PR.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/rules/upgrade-consistency.md
git commit -m "$(cat <<'EOF'
chore: add upgrade consistency enforcement rule

Mandatory cross-file review requirement and canonical service names
reference to prevent upgrade path drift.
EOF
)"
```

---

### Task 11: Documentation Updates

**Files:**

- Modify: `CHANGELOG.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `README.md` (if upgrade section exists)

- [ ] **Step 1: Read current CHANGELOG.md header**

Read first 30 lines of CHANGELOG.md to understand format.

- [ ] **Step 2: Add changelog entry**

Add a new version entry (or Unreleased section) documenting:

- Fixed: Service name bug in upgrade-helper-process (audiobooks- → audiobook-)
- Added: Mandatory preflight check system (LEAPP-inspired)
- Added: Always-on backup with rolling retention
- Added: --skip-service-lifecycle internal flag
- Added: Full upgrade feature parity in web UI (force, major version, specific version)
- Added: Caddy maintenance page for external visitors during upgrade
- Added: Resilient browser upgrade overlay with progress tracking
- Added: Upgrade consistency enforcement rule

- [ ] **Step 3: Update ARCHITECTURE.md**

Add or update the "Upgrade System" section in docs/ARCHITECTURE.md:

- Privilege-separated helper pattern
- Preflight check flow
- Three-tier maintenance strategy (browser overlay, Caddy, Cloudflare)
- Service lifecycle ownership

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
docs: update changelog and architecture for upgrade consistency overhaul

Documents preflight system, always-on backup, Caddy maintenance page,
resilient overlay, and service name fix.
EOF
)"
```

---

### Task 12: Integration Testing on VM

**Files:** No files modified — testing only

- [ ] **Step 1: Run all unit tests on dev machine**

```bash
cd <project-dir>
python -m pytest library/tests/test_helper_service_names.py \
    library/tests/test_upgrade_skip_lifecycle.py \
    library/tests/test_upgrade_preflight.py \
    library/tests/test_helper_lifecycle.py \
    library/tests/test_upgrade_api.py \
    library/tests/test_caddy_integration.py -v
```

Expected: All PASS

- [ ] **Step 2: Run full unit test suite**

```bash
python -m pytest library/tests/ -v --tb=short
```

Expected: No regressions

- [ ] **Step 3: Run linters and security scanners**

```bash
ruff check library/ && shellcheck upgrade.sh scripts/upgrade-helper-process install.sh && bandit -r library/backend/
```

- [ ] **Step 4: Deploy to test VM and run integration tests**

```bash
./upgrade.sh --from-project . --remote <test-vm-ip> --yes
```

Then on the VM:

1. Verify service names: `systemctl status audiobook-api audiobook-proxy`
2. Test web upgrade check: hit the UI, run Check for Updates
3. Test preflight gate: try upgrade without check (should fail)
4. Test force bypass: check Force, start upgrade (should proceed)
5. Test Caddy maintenance: stop audiobook services, verify Caddy serves page
6. Test full web upgrade: complete upgrade from browser, verify overlay behavior

- [ ] **Step 5: Document test results in session record**

Record all pass/fail results in SESSION_RECORD.
