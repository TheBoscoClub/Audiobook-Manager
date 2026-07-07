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

