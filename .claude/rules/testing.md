# Testing — Isolation & Verification

## Dev Machine vs VM

**Dev machine: unit tests, linting, static analysis, code editing ONLY. Integration, API, UI/Playwright, auth and E2E tests MUST run on the test VM.** `/test` Phase 10b (VM Lifecycle) detects pristine state and auto-installs before tests.

### What Runs Where

| Test Type | Where | Example |
|-----------|-------|---------|
| Unit tests | Dev machine | `pytest library/tests/test_metadata.py` |
| Config/lint tests | Dev machine | `pytest library/tests/test_config.py` |
| API integration | VM | `pytest library/tests/test_backoffice_integration.py` |
| UI/Playwright | VM | `pytest library/tests/test_player_navigation_persistence.py` |
| Auth/WebAuthn | Dev (unit) / VM (integration) | Unit mocks OK; real auth flow needs VM |
| Auth lifecycle | VM | `pytest library/tests/test_auth_lifecycle_integration.py` |

## After Syncing Project to Production

After `upgrade.sh`:

1. Wrapper scripts execute: `for cmd in /usr/local/bin/audiobooks-*; do $cmd --help 2>&1 | head -1 || echo "BROKEN: $cmd"; done`
2. API responds: `curl -s http://localhost:5001/api/system/version`
3. Web UI loads and buttons work

## Running pytest on the Deployed VM (Installed Environment)

Against `/opt/audiobooks/library`, `webauthn` etc. exist only in the venv and `sudo` drops the environment:

```bash
cd /opt/audiobooks/library
sudo -E -u audiobooks env PYTHONPATH=/opt/audiobooks/library/venv/lib/python3.14/site-packages \
    pytest --vm -k "test_api_behavioral or sort" --tb=short -q
```

Without `-E` + explicit `PYTHONPATH`, `test_admin_activity_api.py` fails collection with `ModuleNotFoundError: No module named 'webauthn'`.

## Test VM Lifecycle — Always Shut Down at End of /test

**`test-audiobook-cachyos` is EXCLUSIVELY for `/test` audits** (no other workload or user), so it MUST be shut down at the end of every `/test` audit, even if `/test` did not start it. This overrides the generic "preserve VMs /test didn't start" logic in `~/.claude/skills/test-phases/phase-11-cleanup.md`, which is right for shared VMs only.

**Authoritative source (the contract)**: `~/.claude/config/project-vm-map.json` sets `"post_test_restore": true` on both `vms.test-audiobook-cachyos` and `projects.Audiobook-Manager`. phase-11-cleanup.md reads it for the current project and, if any matching VM has `post_test_restore: true`, runs `virsh shutdown` unconditionally.

| VM | post_test_restore | Phase 11 cleanup action |
|----|--------------------|----------------|
| `test-audiobook-cachyos` | true | **ALWAYS shut down** (revert to pristine BTRFS snapshot on next /test start) |
| `qa-audiobook-cachyos` | false | Leave running (QA mirror for released-version smoke testing) |
| `dev-audiobook-cachyos` | false | Leave running ONLY if user started it; user owns dev VM lifecycle |

**Verify at end of every /test run**: `sudo virsh domstate test-audiobook-cachyos` must print `shut off`. If not, Phase 11 has a bug — fix it; do not shut down manually as a workaround.

## CRITICAL: Test/QA Data Isolation

**No test VM, QA VM, or test/QA Docker container may have LIVE ACCESS (mounts) to production storage.** Copying production data *onto* the VM's own disk is fine (isolated); live filesystem links are not.

| Action | Allowed? |
|--------|----------|
| VM creates own fresh DB via `install.sh` | **Yes** |
| `scp`/`rsync` production DB into VM | **Yes** (copy) |
| Copy production library onto VM disk (up to ~275GB) | **Yes** |
| Mount host production paths via NFS/CIFS/virtiofs | **NEVER** |
| Docker `-v` mount to host production paths | **NEVER** |

| Environment | Databases | Audiobook Library | Configuration |
|-------------|-----------|-------------------|---------------|
| **Production** (host) | `/var/lib/audiobooks/db/*.db` | `${AUDIOBOOKS_LIBRARY}` (full) | `/etc/audiobooks/` |
| **Test VM** / **QA VM** | Own DBs on VM disk (fresh or copied) | Own library on VM disk (<275GB) | Own config on VM disk |
| **Docker test** | Ephemeral in-container DB | Sample data via volume or none | Container env vars only |

### Prohibited actions

- **NEVER** mount the host's `${AUDIOBOOKS_DATA}` tree into a test/QA VM via NFS, CIFS, virtiofs, or virtio-9p
- **NEVER** mount production database paths into a VM or Docker container as a live filesystem
- **NEVER** configure Docker `-v` to bind-mount host production paths at runtime
- **NEVER** give test/QA environments write access to production storage through any mechanism

### Tests must not resolve production paths either

`library/tests/conftest.py` pins `COVER_DIR`, `AUDIOBOOKS_COVERS`, `AUDIOBOOKS_LIBRARY` and `AUDIOBOOKS_DATA` into a per-run temp tree **at module scope** (not a fixture — `library/config.py` resolves them at import time). Do not remove that block; do not add tests reading these from the ambient environment.

Why: `import_to_db._cleanup_orphaned_covers()` **deletes** every file in `COVER_DIR` the DB does not reference — with a temp test DB, every cover. On 2026-08-27 a run with config fallen back to production defaults attempted exactly that, stopped only by filesystem permissions (which the `sudo -E -u audiobooks … pytest` invocation above would NOT have). Both guards must stay: the conftest pin, and the refusal in `_cleanup_orphaned_covers()` when a sweep would delete every file present (`Audiobook-Manager-d40`).

### Release leak prevention (COPYRIGHT/LICENSE CRITICAL)

Production audiobooks are personally owned licensed content; leaking them into a release (GitHub, Docker registry, tarball) exposes private data and creates copyright/trademark liability.

- **Docker test containers**: production data copied in MUST be cleaned up (container removed) in Phase 9c cleanup or Phase 11, BEFORE `/test` ends
- **Docker test images**: NEVER bake production data in via `COPY`; use runtime `-v` mounts or `docker cp`
- **Project working tree**: NEVER copy production data (audiobooks, databases, configs) into the project; if it happens, remove it BEFORE any commit or release
- **Pre-release guard**: `/git-release` separation check scans release artifacts for production paths — the last line of defense

## Browser for UI/E2E Testing

**Use Brave for all UI/E2E testing.** On a test/QA VM without it: `sudo pacman -S brave-bin --noconfirm` (chaotic-aur), plus codecs `sudo pacman -S opus libopus --noconfirm` (Brave is Chromium-based with full Opus/WebM support). Playwright: `chromium` channel pointed at the Brave binary, or launch with `--ignore-https-errors` for self-signed certs.

## Version-Gated Test Markers (v8 Separation)

`@pytest.mark.v8` tests auto-skip when the `VERSION` major < 8 (`conftest.py::pytest_collection_modifyitems` reads `VERSION`; no CLI flag).

- v8 tests go in own modules (`test_v8_feature_name.py`) OR carry `@pytest.mark.v8`
- v7 modules carry forward into v8 unchanged (foundational behavior)
- Mark `v8` only for features that DON'T EXIST in v7; if v8 replaces a v7 feature, keep the v7 test and write a new v8 one
- New versions (`v9`, `v10`): add marker to `pytest.ini`, register in `pytest_configure`, add gating block in `pytest_collection_modifyitems`

## Cross-Component Holistic Testing (Mandatory)

Every test — unit, integration, QA, or /test audit — must verify cross-component effects; subsystems (API, web UI, scanner, converter, services, database, auth) are tightly coupled.

| Change Area | Must Also Verify |
|-------------|-----------------|
| API endpoint changes | Web UI pages that call it, CLI wrappers, systemd services |
| Database schema/queries | Scanner, API, web UI library views, converter pipeline |
| Auth/WebAuthn changes | API auth middleware, web login flow, session persistence |
| Scanner/metadata changes | Library view (titles, covers, durations), API search results |
| Converter pipeline changes | Mover service, library file structure, metadata consistency |
| Config changes | All services that read config, upgrade.sh, install.sh |
| Systemd service changes | `audiobook.target` ordering, API/proxy startup, upgrade flow |

## Verified Proof Required

Global `verification.md` applies. Project proof forms: API → `curl` output with HTTP status + body; services → `systemctl status` showing `active (running)`; web UI → HTTP code + content or Playwright screenshot; tests → `pytest` pass/fail counts + coverage %; upgrade → version file before/after + service status after restart; DB → `PRAGMA integrity_check` output + expected row counts.

**FVP Protocol**: every fix in a /test audit emits an FVP (Fix-Verify-Proof) block — exact command, before/after output, collateral damage check — per the /test skill. A fix without one is incomplete.

## AI Self-Promotion Prohibition

Global `git-commits.md` applies to all code, docs, commits, templates and metadata here (also: no Anthropic URLs `claude.ai`/`anthropic.com` as attribution, no AI branding emojis/badges). /test (Phase 5c + Phase 8) and QA modules (Step 6h) scan and remove these; remove any new instance before commit.

## Testing & Validation Notes

When running `/test`: **DO NOT** access production data from project code or create symlinks from application to project; **DO** use `./library/testdata/`, verify the application works independently if the project is deleted, and update the application only via `./upgrade.sh`, never manual symlinks.
