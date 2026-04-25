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
| SAMPLER.md | `./docs/SAMPLER.md` |
| EMAIL-SETUP.md | `./docs/EMAIL-SETUP.md` |
| SERVERLESS-OPS.md | `./docs/SERVERLESS-OPS.md` |
| RCA-v8.3.8.6-chinese-audio-silence.md | `./docs/RCA-v8.3.8.6-chinese-audio-silence.md` |
| CONTRIBUTING.md | `./CONTRIBUTING.md` |

## Future Improvements

| Item | Priority | Notes |
|------|----------|-------|
| ~~Test Coverage~~ | ~~Medium~~ | **DONE** — 95.66% coverage (3305 tests). Previously-low modules now covered: `proxy_server.py` (95%), `cli.py`/`inbox_cli.py`/`notify_cli.py` (from 0%), all `utilities_ops` modules (93-100%), `maintenance_tasks` (all covered), `audit.py` (covered). |
| ~~Cover Art Resolver~~ | ~~Medium~~ | **DONE** — Only 1 book was missing (not ~642). Manually fixed + built tiered resolver (`scanner/utils/cover_resolver.py`: Audible → Open Library → Google Books) as fallback in `extract_cover_art()`. |
| ~~Hide shell.html from URL~~ | ~~Low~~ | **DONE** — `proxy_server.py` serves shell.html content at `/` directly. `/shell.html` redirects 301 → `/`. |
| ~~Mobile player bottom clipping~~ | ~~Medium~~ | **DONE** — Added `env(safe-area-inset-bottom)` padding to `#shell-player` in `shell.css`. Works across Safari, Chrome, Firefox mobile. |
| ~~Email Setup Guide~~ | ~~Low~~ | **DONE (v8.3.8)** — `docs/EMAIL-SETUP.md` covers Resend, Gmail, Outlook, Protonmail Bridge, generic SMTP, mailx/s-nail smoke-test, plus STARTTLS/implicit-SSL/plaintext decision matrix and common failure-mode table. |
| ~~Profile preference live-apply~~ | ~~Low~~ | **DONE (v8.3.8)** — `account.js::saveBrowsingPref` dispatches `audiobooks:preference-changed` CustomEvent; `library.js::_wirePreferenceLiveApply` routes by key (`view_mode` → CSS class toggle, `sort_order`/`items_per_page`/`content_filter` → re-apply + `loadAudiobooks`). No hard refresh required. |
| ~~Cachebust stamp automation~~ | ~~High~~ | **DONE (v8.3.8)** — `scripts/bump-cachebust.sh` rewrites every `?v=<stamp>` in `web-v2/*.html` to a single per-deploy epoch stamp. Invoked by both `upgrade.sh` (after HTML sync, before service restart) and `install.sh`. Replaces the manual ?v= bumping that periodically caused stale-JS incidents (v8.3.4 qalib 2000-ID URL-overflow 400 being one). |
| ~~Data migrations framework for `upgrade.sh`~~ | ~~Medium~~ | **DONE** — `data-migrations/` directory parallel to `config-migrations/`, version-gated via `MIN_VERSION` in each script. `upgrade.sh` runs migrations only when crossing the declared boundary; `install.sh` runs all unconditionally on fresh installs. First migration: `001_podcast_detection.sh` (v8.0.3 boundary). |


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
