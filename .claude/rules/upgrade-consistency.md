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

## New-Script Wiring Enforcement (MANDATORY — added 2026-04-17)

Every new `scripts/*.py` introduced by a release MUST have ALL of the following
before the release is eligible for `/git-release`:

1. A systemd unit (`.service`, plus `.timer` if scheduled) in `systemd/`
2. An entry in `scripts/install-manifest.sh` (MANIFEST_FILES)
3. A copy/install line in `install.sh`
4. A copy/upgrade line in `upgrade.sh`
5. A dispatch hook from wherever it is supposed to run (daemon, API, timer)
6. **OR** a committed exception comment at the top of the script explaining why
   it is a stand-alone tool not wired into the service graph

**Mental review does NOT count.** Each file in the table above MUST be physically
opened and traced through for every release. If a file was not touched, that
confirmation must appear in the release staging notes — not in Claude's head.

**Why**: v8.3.0/v8.3.1 shipped `scripts/stream-translate-worker.py` with zero
wiring — no systemd unit, no install.sh, no upgrade.sh, no manifest entry, no
dispatch. `streaming_segments` rows inserted by the streaming API had nobody to
read them. The maintainer's real-time Chinese translation demo on prod failed
with a 5-minute "排队中" spinner. The CHANGELOG claimed the pipeline was
shipped; the installed application never received the worker. See memory
`feedback_upgrade_consistency_enforcement.md` for full post-mortem.

## New-Table Wiring Enforcement (MANDATORY — added 2026-04-17)

Every new DB migration that inserts rows requiring downstream processing MUST
have a grep-confirmed reader in an actively-running process (daemon, worker, API
endpoint that fires on a timer, etc.). Orphan tables — rows inserted by one
process that nothing ever reads — are a release blocker.

**How to check**: After the migration is committed, run
`rg -l '<new_table_name>'` and confirm at least one hit is a file that systemd
actually runs. If the only hits are tests, models, and the inserting process
itself, the feature is half-installed.
