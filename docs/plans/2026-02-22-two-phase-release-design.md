# Two-Phase Release Workflow Design

**Date**: 2026-02-22
**Status**: Implemented
**Skill**: `/git-release`

## Problem

The `/git-release` skill pushes to GitHub immediately. There's no way to stage a release locally, deploy to a test VM, run full E2E testing, then publish the same release to GitHub only after verification passes.

## Solution

Two new flags: `--local` (stage) and `--promote` (publish).

### `--local` — Stage Phase

Runs the full release workflow (validation, changelog, version bump, commit, tag) but stops before pushing. Writes a `.staged-release` breadcrumb file containing version, tag, commit SHA, and timestamp.

The user then deploys from the local repo to their test environment, runs E2E tests, and verifies everything works.

### `--promote` — Publish Phase

Reads the `.staged-release` breadcrumb, verifies the tag and commit still exist and the tree is clean, then pushes the existing commit and tag to GitHub and creates the GitHub release. No re-validation — the user already tested.

### Key Design Decisions

1. **No validation on promote** — the whole point is that the user tested between stage and promote
2. **Mutually exclusive flags** — `--local` and `--promote` cannot be combined
3. **`--promote` takes no version argument** — it reads from the breadcrumb
4. **`.staged-release` is ephemeral** — deleted after successful promote, should be in `.gitignore`
5. **Composable with existing flags** — `--local --local-docker` and `--local --skip-validation` are valid

### Files Modified

- `~/.claude/commands/git-release.md` — all changes in this single skill file
