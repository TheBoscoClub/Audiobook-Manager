# Audiobook Manager Project

## Core Rules (details in .claude/rules/)

- `rules/paths-and-separation.md` — no hardcoded paths; project and installed app completely separate
- `rules/testing.md` — dev machine vs test VM, prod-data isolation, verification
- `rules/audio-metadata.md` — Opus metadata location
- `rules/vm-lifecycle.md` — VMs boot on demand, shut down after
- `rules/upgrade-consistency.md` — upgrade changes touch all upgrade files; new scripts/tables wired

## Source File Protection

**CRITICAL: Never delete source files (.aaxc) unless** they are verified checksum duplicates (matching partial MD5 hash) — and even then delete only ONE copy; always preserve at least one original.

## Systemd Services

All units are `audiobook-*` (list: `systemd/`; full names: `.claude/docs/rules-reference/claude-md.md`).

## Current Version

See `VERSION` file. User/group: `audiobooks:audiobooks`

## Project Documentation

Root: `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`; `docs/` (index: `.claude/docs/rules-reference/claude-md.md`).
