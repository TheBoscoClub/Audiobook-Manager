# Audiobook Manager Project

## Core Rules (details in .claude/rules/)

1. **NO HARDCODED PATHS** — All paths MUST use configuration variables from `lib/audiobook-config.sh`. See `rules/paths-and-separation.md`.
2. **PROJECT/APP SEPARATION** — Project and installed application are COMPLETELY SEPARATE with zero dependencies. See `rules/paths-and-separation.md`.
3. **VM TESTING** — Dev machine for unit tests only. All integration/API/UI tests on `test-audiobook-cachyos`. See `rules/testing.md`.
4. **OPUS METADATA** — Opus stores metadata in `streams[0].tags`, not `format.tags`. Check both. See `rules/audio-metadata.md`.

## Source File Protection

**CRITICAL: Never delete source files (.aaxc) unless:**

1. They are verified checksum duplicates (matching partial MD5 hash)
2. Even for duplicates, only delete ONE copy — always preserve at least one original

## Systemd Services

`audiobook.target`, `audiobook-api.service`, `audiobook-proxy.service`, `audiobook-redirect.service`, `audiobook-converter.service`, `audiobook-mover.service`, `audiobook-scheduler.service`, `audiobook-downloader.service/.timer`, `audiobook-enrichment.service/.timer`, `audiobook-shutdown-saver.service`, `audiobook-upgrade-helper.service`, `audiobook-upgrade-helper.path`

## Current Version

See `VERSION` file. User/group: `audiobooks:audiobooks`

## Project Documentation

| Document | Location |
|----------|----------|
| README.md | `./README.md` |
| CHANGELOG.md | `./CHANGELOG.md` |
| ARCHITECTURE.md | `./docs/ARCHITECTURE.md` |
| POSITION_SYNC.md | `./docs/POSITION_SYNC.md` |
| SECURE_REMOTE_ACCESS_SPEC.md | `./docs/SECURE_REMOTE_ACCESS_SPEC.md` |
| AUTH_RUNBOOK.md | `./docs/AUTH_RUNBOOK.md` |
| AUTH_FAILURE_MODES.md | `./docs/AUTH_FAILURE_MODES.md` |
| CSS-CUSTOMIZATION.md | `./docs/CSS-CUSTOMIZATION.md` |
| TROUBLESHOOTING.md | `./docs/TROUBLESHOOTING.md` |
| INTERMITTENT-FAILURES-ANALYSIS.md | `./docs/INTERMITTENT-FAILURES-ANALYSIS.md` |
| SECURITY-INTEGRATION-PLAN.md | `./docs/SECURITY-INTEGRATION-PLAN.md` |
| INSTALLER-ARCHITECTURE.md | `./docs/INSTALLER-ARCHITECTURE.md` |
| CONTENT-CLASSIFICATION-DRIFT.md | `./docs/CONTENT-CLASSIFICATION-DRIFT.md` |
| MULTI-LANGUAGE-SETUP.md | `./docs/MULTI-LANGUAGE-SETUP.md` |
| STREAMING-TRANSLATION.md | `./docs/STREAMING-TRANSLATION.md` |
| STREAMING-TRANSLATION.zh-Hans.md | `./docs/STREAMING-TRANSLATION.zh-Hans.md` |
| GPU-FLEET-OPS.md | `./docs/GPU-FLEET-OPS.md` |
| CONTRIBUTING.md | `./CONTRIBUTING.md` |

## Future Improvements

| Item | Priority | Notes |
|------|----------|-------|
| ~~Test Coverage~~ | ~~Medium~~ | **DONE** — 95.66% coverage (3305 tests). Previously-low modules now covered: `proxy_server.py` (95%), `cli.py`/`inbox_cli.py`/`notify_cli.py` (from 0%), all `utilities_ops` modules (93-100%), `maintenance_tasks` (all covered), `audit.py` (covered). |
| ~~Cover Art Resolver~~ | ~~Medium~~ | **DONE** — Only 1 book was missing (not ~642). Manually fixed + built tiered resolver (`scanner/utils/cover_resolver.py`: Audible → Open Library → Google Books) as fallback in `extract_cover_art()`. |
| ~~Hide shell.html from URL~~ | ~~Low~~ | **DONE** — `proxy_server.py` serves shell.html content at `/` directly. `/shell.html` redirects 301 → `/`. |
| ~~Mobile player bottom clipping~~ | ~~Medium~~ | **DONE** — Added `env(safe-area-inset-bottom)` padding to `#shell-player` in `shell.css`. Works across Safari, Chrome, Firefox mobile. |
| Email Setup Guide | Low | Add `docs/EMAIL-SETUP.md` covering end-to-end SMTP config with examples for Protonmail Bridge, mailx, s-nail, Outlook, Gmail, Resend, and generic SMTP. Include TLS/STARTTLS/SSL transport security options and when to use each. |
| Profile preference live-apply | Low | **Deferred until after Localization-RND merges to main.** Changing Grid/List view preference in the user profile (and possibly other profile options) requires a hard browser refresh to take effect. Exists in both `main` and `Localization-RND` — fix in `main` ONLY after the localization branch is merged, to avoid cherry-picking. Investigate `library/web-v2/js/account.js` / profile preference save path — likely missing a reactive dispatch that re-renders the library grid on preference change. |
| ~~Data migrations framework for `upgrade.sh`~~ | ~~Medium~~ | **DONE** — `data-migrations/` directory parallel to `config-migrations/`, version-gated via `MIN_VERSION` in each script. `upgrade.sh` runs migrations only when crossing the declared boundary; `install.sh` runs all unconditionally on fresh installs. First migration: `001_podcast_detection.sh` (v8.0.3 boundary). |
