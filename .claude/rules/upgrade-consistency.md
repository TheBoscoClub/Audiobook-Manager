# Upgrade System Consistency — Mandatory Cross-File Review

A change to upgrade functionality in ANY of these requires review and update of ALL:
`upgrade.sh` (CLI engine), `scripts/upgrade-helper-process` (privileged bridge for web upgrades), `library/backend/api_modular/utilities_system.py` (API), `library/web-v2/utilities.html` (UI markup), `library/web-v2/js/utilities.js` (UI logic), `install.sh` (first install), `caddy/audiobooks.conf` (Caddy maintenance routing), `caddy/maintenance.html` (external maintenance page).

## Canonical Service Names

All systemd services are singular `audiobook-*`; any `audiobooks-*` (plural) is a bug. Authoritative list: `systemd/*.service`.

## Upgrade Feature Parity

Every `upgrade.sh` CLI option MUST exist in the web UI — no gatekeeping. A new flag needs its UI control, API field and helper parsing in the same commit or PR.

## New-Script Wiring Enforcement (MANDATORY — added 2026-04-17)

Every new `scripts/*.py` in a release needs ALL of, before `/git-release`:

1. systemd unit (`.service`, plus `.timer` if scheduled) in `systemd/`
2. entry in `scripts/install-manifest.sh` (MANIFEST_FILES)
3. copy/install line in `install.sh`
4. copy/upgrade line in `upgrade.sh`
5. dispatch hook from where it runs (daemon, API, timer)
6. **OR** a committed exception comment atop the script explaining why it is stand-alone

**Mental review does NOT count.** Physically open and trace every file above each release; an untouched file's confirmation goes in the release staging notes.

**Why**: v8.3.0/v8.3.1 shipped `scripts/stream-translate-worker.py` with zero wiring; `streaming_segments` rows had no reader and the prod Chinese translation demo hung on a 5-minute "排队中" spinner while CHANGELOG claimed it shipped. Post-mortem: memory `feedback_upgrade_consistency_enforcement.md`.

## New-Table Wiring Enforcement (MANDATORY — added 2026-04-17)

A migration inserting rows that need downstream processing MUST have a grep-confirmed reader in an actively running process (daemon, worker, timer-fired endpoint). Orphan tables are a release blocker. Check: `rg -l '<new_table_name>'` — at least one hit must be a file systemd actually runs; only tests/models/the inserter = half-installed.
