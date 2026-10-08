# Audiobook Manager Project

## Core Rules (details in .claude/rules/)

- `rules/paths-and-separation.md` — NO HARDCODED PATHS (use `lib/audiobook-config.sh` vars); project and installed app COMPLETELY SEPARATE, zero dependencies
- `rules/testing.md` — dev machine = unit tests only; all integration/API/UI tests on `test-audiobook-cachyos`; prod-data isolation
- `rules/audio-metadata.md` — Opus metadata is in `streams[0].tags`, not `format.tags`; check both
- `rules/vm-lifecycle.md` — boot VMs on demand with `vm-session up`, shut down after; never leave one idling
- `rules/upgrade-consistency.md` — upgrade changes touch ALL upgrade files; new scripts/tables must be wired

## Source File Protection

**CRITICAL: Never delete source files (.aaxc) unless** they are verified checksum duplicates (matching partial MD5 hash) — and even then delete only ONE copy; always preserve at least one original.

## Systemd Services

`audiobook.target`, `audiobook-api.service`, `audiobook-proxy.service`, `audiobook-redirect.service`, `audiobook-converter.service`, `audiobook-mover.service`, `audiobook-scheduler.service`, `audiobook-downloader.service/.timer`, `audiobook-enrichment.service/.timer`, `audiobook-stream-translate.service`, `audiobook-translation-monitor-live.service/.timer`, `audiobook-translation-monitor-sampler.service/.timer`, `audiobook-shutdown-saver.service`, `audiobook-upgrade-helper.service`, `audiobook-upgrade-helper.path`

## Current Version

See `VERSION` file. User/group: `audiobooks:audiobooks`

## Project Documentation

- Root: `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`
- `docs/`: `ARCHITECTURE.md`, `POSITION_SYNC.md`, `SECURE_REMOTE_ACCESS_SPEC.md`, `AUTH_RUNBOOK.md`, `AUTH_FAILURE_MODES.md`, `CSS-CUSTOMIZATION.md`, `TROUBLESHOOTING.md`, `INTERMITTENT-FAILURES-ANALYSIS.md`, `SECURITY-INTEGRATION-PLAN.md`, `INSTALLER-ARCHITECTURE.md`, `CONTENT-CLASSIFICATION-DRIFT.md`, `MULTI-LANGUAGE-SETUP.md`, `STREAMING-TRANSLATION.md`, `STREAMING-TRANSLATION.zh-Hans.md`, `SAMPLER.md`, `TRANSLATION-MONITOR.md`, `EMAIL-SETUP.md`, `SERVERLESS-OPS.md`, `STORAGE-CAPACITY.md`, `RCA-v8.3.8.6-chinese-audio-silence.md`
