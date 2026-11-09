# Testing — Isolation & Verification — reference (NOT auto-loaded)

Detail moved out of `.claude/rules/testing.md`, same headings. Verbatim; the rule file keeps the binding text.

## Dev Machine vs VM

### What Runs Where

| Test Type | Where | Example |

|-----------|-------|---------|

| Unit tests | Dev machine | `pytest library/tests/test_metadata.py` |

| Config/lint tests | Dev machine | `pytest library/tests/test_config.py` |

| API integration | VM | `pytest library/tests/test_backoffice_integration.py` |

| UI/Playwright | VM | `pytest library/tests/test_player_navigation_persistence.py` |

| Auth/WebAuthn | Dev (unit) / VM (integration) | Unit mocks OK; real auth flow needs VM |

| Auth lifecycle | VM | `pytest library/tests/test_auth_lifecycle_integration.py` |

## Running pytest on the Deployed VM (Installed Environment)

Without `-E` + explicit `PYTHONPATH`, `test_admin_activity_api.py` fails collection with `ModuleNotFoundError: No module named 'webauthn'`.

## Test VM Lifecycle — Always Shut Down at End of /test

**Authoritative source (the contract)**: the `/test` VM map sets `"post_test_restore": true` on both the test VM's `vms.` entry and `projects.Audiobook-Manager`. phase-11-cleanup.md reads it for the current project and, if any matching VM has `post_test_restore: true`, runs `virsh shutdown` unconditionally.

| test VM | true | **ALWAYS shut down** (revert to pristine BTRFS snapshot on next /test start) |

| QA VM | false | Phase 11 does not touch it (QA mirror for released-version smoke testing). *Superseded 2026-11-01: was "Leave running"; Claude never leaves it running, the operator may; see bd `Audiobook-Manager-8vw`.* |

## CRITICAL: Test/QA Data Isolation

| Environment | Databases | Audiobook Library | Configuration |

|-------------|-----------|-------------------|---------------|

| **Production** (host) | `/var/lib/audiobooks/db/*.db` | `${AUDIOBOOKS_LIBRARY}` (full) | `/etc/audiobooks/` |

| **Test VM** / **QA VM** | Own DBs on VM disk (fresh or copied) | Own library on VM disk (<275GB) | Own config on VM disk |

| **Docker test** | Ephemeral in-container DB | Sample data via volume or none | Container env vars only |

### Tests must not resolve production paths either

Why: `import_to_db._cleanup_orphaned_covers()` **deletes** every file in `COVER_DIR` the DB does not reference — with a temp test DB, every cover. On 2026-08-27 a run with config fallen back to production defaults attempted exactly that, stopped only by filesystem permissions (which the `sudo -E -u audiobooks … pytest` invocation above would NOT have). Both guards must stay: the conftest pin, and the refusal in `_cleanup_orphaned_covers()` when a sweep would delete every file present (`Audiobook-Manager-d40`).

### Release leak prevention (COPYRIGHT/LICENSE CRITICAL)

Production audiobooks are personally owned licensed content; leaking them into a release (GitHub, Docker registry, tarball) exposes private data and creates copyright/trademark liability.

## Browser for UI/E2E Testing

**Use Chromium for all UI/E2E testing** (official Arch/CachyOS repo, never AUR). On a test/QA VM without it: `sudo pacman -S chromium --noconfirm`; Opus/WebM playback is built in. Playwright: `chromium` channel with `executable_path="/usr/bin/chromium"`, or launch with `--ignore-https-errors` for self-signed certs. Brave was the test browser until 2026-11-09; it moved off AUR (`brave-bin`) to the repo package.
