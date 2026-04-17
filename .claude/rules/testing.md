# Testing — Isolation & Verification

## Dev Machine vs VM

**Dev machine is for unit tests and code editing ONLY. All integration, API, UI, and E2E tests MUST run against the dedicated test VM.**

- **Dev machine**: Unit tests, linting, static analysis, code editing
- **VM**: Integration tests, API tests, UI/Playwright tests, auth tests, E2E tests
- **`/test` handles this automatically**: Phase 10b (VM Lifecycle) detects pristine state and auto-installs before tests run

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

After running `upgrade.sh`:

1. Verify all wrapper scripts execute: `for cmd in /usr/local/bin/audiobooks-*; do $cmd --help 2>&1 | head -1 || echo "BROKEN: $cmd"; done`
2. Verify API responds: `curl -s http://localhost:5001/api/system/version`
3. Verify web UI loads and buttons work

## CRITICAL: Test/QA Data Isolation

**No test VM, QA VM, or test/QA Docker container may have LIVE ACCESS (mounts) to production storage.**

Copying production data *into* a test/QA environment is fine — once data is on the VM's own disk, it's fully isolated. The prohibition is against live filesystem links that let test environments read or write production storage directly.

### What's allowed vs forbidden

| Action | Allowed? | Why |
|--------|----------|-----|
| VM creates own fresh DB via `install.sh` | **Yes** | Fully isolated on VM disk |
| `scp`/`rsync` production DB into VM | **Yes** | It's a copy — isolated on VM disk |
| Copy production library into VM disk | **Yes** | Isolated copy, up to ~275GB is fine |
| Mount host production paths via NFS/CIFS/virtiofs | **NEVER** | Live access to production filesystem |
| Docker `-v` mount to host production paths | **NEVER** | Live access to production filesystem |

### What each environment gets

| Environment | Databases | Audiobook Library | Configuration |
|-------------|-----------|-------------------|---------------|
| **Production** (host) | `/var/lib/audiobooks/db/*.db` | `${AUDIOBOOKS_LIBRARY}` (full) | `/etc/audiobooks/` |
| **Test VM** | Own DBs on VM disk (fresh or copied) | Own library on VM disk (<275GB) | Own config on VM disk |
| **QA VM** | Own DBs on VM disk (fresh or copied) | Own library on VM disk (<275GB) | Own config on VM disk |
| **Docker test** | Ephemeral in-container DB | Sample data via volume or none | Container env vars only |

### Prohibited actions

- **NEVER** mount the host's `${AUDIOBOOKS_DATA}` tree into a test/QA VM via NFS, CIFS, virtiofs, or virtio-9p
- **NEVER** mount production database paths into a VM or Docker container as a live filesystem
- **NEVER** configure Docker `-v` to bind-mount host production paths at runtime
- **NEVER** give test/QA environments write access to production storage through any mechanism

### Release leak prevention (COPYRIGHT/LICENSE CRITICAL)

Production audiobook files are personally owned and licensed content. Accidentally including them in a release (GitHub, Docker registry, tarball) would expose private data and create copyright/trademark liability.

**Mandatory safeguards:**

- **Docker test containers**: Any production data copied into a test container MUST be cleaned up (container removed) during Phase 9c cleanup or Phase 11, BEFORE `/test` formally ends
- **Docker test images**: NEVER build a Docker image with production data baked in via `COPY`. Use runtime `-v` mounts or `docker cp` for test data — these don't persist in the image
- **Project working tree**: NEVER copy production data (audiobooks, databases, configs) into the project directory. If this happens accidentally, remove it BEFORE any commit or release operation
- **Pre-release guard**: `/git-release` checks for production paths in release artifacts (see separation check in git-release skill). This is the last line of defense.

## Browser for UI/E2E Testing

**Use Brave browser for all UI and E2E testing.** If Brave is not installed on a test/QA VM, install it before running browser tests:

```bash
# CachyOS/Arch: install from chaotic-aur
sudo pacman -S brave-bin --noconfirm
```

Brave is Chromium-based with full Opus/WebM codec support. Also ensure codec libraries are present: `sudo pacman -S opus libopus --noconfirm`.

For Playwright, use the `chromium` channel pointing to the Brave binary or launch with `--ignore-https-errors` for self-signed cert environments.

## Version-Gated Test Markers (v8 Separation)

Tests for future major versions use version-gated markers that auto-skip based on the `VERSION` file:

```python
@pytest.mark.v8
def test_new_v8_feature():
    """This test only runs when VERSION major >= 8."""
    ...
```

**How it works:**

- `conftest.py::pytest_collection_modifyitems` reads `VERSION`, extracts major version
- Tests marked `@pytest.mark.v8` auto-skip when major < 8
- No CLI flag needed — version detection is automatic

**Rules for v8 test separation:**

- v8 tests go in their own modules (e.g., `test_v8_feature_name.py`) OR use the `@pytest.mark.v8` marker on individual tests
- v7 test modules carry forward into v8 unchanged — they test foundational behavior
- Only mark tests as `v8` when they test features that DON'T EXIST in v7
- If a v8 feature completely replaces a v7 feature, the v7 test stays (for v7 releases) and a new v8 test is written

**Adding future versions:** To add `v9`, `v10`, etc., follow the same pattern — add marker to `pytest.ini`, register in `pytest_configure`, add gating block in `pytest_collection_modifyitems`.

## Cross-Component Holistic Testing (Mandatory)

**Every test — unit, integration, QA, or /test audit — must include cross-component verification.** This project has tightly coupled subsystems (API, web UI, scanner, converter, services, database, auth) where changes to one component frequently break another in non-obvious ways.

**Cross-component checks required for all test types:**

| Change Area | Must Also Verify |
|-------------|-----------------|
| API endpoint changes | Web UI pages that call it, CLI wrappers, systemd services |
| Database schema/queries | Scanner, API, web UI library views, converter pipeline |
| Auth/WebAuthn changes | API auth middleware, web login flow, session persistence |
| Scanner/metadata changes | Library view (titles, covers, durations), API search results |
| Converter pipeline changes | Mover service, library file structure, metadata consistency |
| Config changes | All services that read config, upgrade.sh, install.sh |
| Systemd service changes | `audiobook.target` ordering, API/proxy startup, upgrade flow |

**The question every test must answer**: "Did this change break something else I didn't know was related?"

## Verified Proof Required

**No test is complete or successful without verified, verifiable proof.** Every test result MUST be backed by a proof artifact — command output, API response, HTTP status code, screenshot, or log excerpt — that demonstrates the claimed result.

| Claim | Required Proof |
|-------|---------------|
| "API works" | Actual `curl` output with HTTP status and response body |
| "Services are running" | `systemctl status` output showing `active (running)` |
| "Web UI loads" | HTTP response code + page content (or screenshot via Playwright) |
| "Tests pass" | `pytest` output with pass/fail counts and coverage percentage |
| "Upgrade succeeded" | Version file before and after, service status after restart |
| "DB is consistent" | `PRAGMA integrity_check` output, row counts matching expectations |

**"It should work" is not proof. "The code looks correct" is not proof. Only observable output is proof.**

**FVP Protocol**: Every individual fix during a /test audit must emit a structured FVP proof block (Fix-Verify-Proof) with the exact command executed, before/after output, and collateral damage check. See the FVP Protocol in the /test skill for the mandatory format. A fix without a proof block is an incomplete fix.

## AI Self-Promotion Prohibition

**All code, documentation, commits, templates, and metadata in this project must be free of AI-generated self-promotion, advertising, branding, and attribution.** This includes:

- `Co-Authored-By:` lines referencing Claude, Anthropic, or any AI tool
- "Generated with Claude Code", "Built with Claude", "Powered by Anthropic" — anywhere
- Anthropic URLs (`claude.ai`, `anthropic.com`) injected as attribution
- AI branding emojis or badges in documentation
- "AI-assisted" or "AI-generated" attribution in any file

The /test audit (Phase 5c + Phase 8) and QA modules (Step 6h) scan for and remove these automatically. Any new instance introduced by a code generation tool must be caught and removed before commit.

## Testing & Validation Notes

When running `/test`:

1. **DO NOT** access production data from project code
2. **DO NOT** create symlinks from application to project
3. **DO** use test data in `./library/testdata/`
4. **DO** verify application works independently if project is deleted
5. **DO** use `./upgrade.sh` to update the application, never manual symlinks
