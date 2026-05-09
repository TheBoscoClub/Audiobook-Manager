# Agent Instructions

This file tells AI coding agents (Claude Code, Codex, etc.) how to operate in this project. The rules in `~/.claude/CLAUDE.md` and this project's own `CLAUDE.md` are the canonical authority. This file is a quick orientation, not a substitute for those.

## Tool Boundaries (Read This First)

| Job | Tool |
|---|---|
| In-conversation todos for this session | `TaskCreate` / `TaskUpdate` / `TaskList` |
| Cross-session ephemeral state and recovery | `.claude-checkpoint-notes.md` (written by `/checkpoint`) |
| Cross-conversation knowledge spanning all projects | `MEMORY.md` and the auto-memory system |
| Cross-session task graph with explicit dependencies | `bd` (beads) — see below |
| Session lifecycle | `/checkpoint`, `/close`, `/test`, `/git-release` |

`TaskCreate`, `MEMORY.md`, `/checkpoint`, and `/close` are **not replaced by bd**. Each owns a distinct scope. Use bd only where it earns its keep — long-horizon dependency-graphed work — and leave the others alone.

For the full policy, read `~/.claude/rules/beads.md`.

## Non-Interactive Shell Commands

ALWAYS use non-interactive flags with file operations to avoid hanging on confirmation prompts. Some shells alias `cp`, `mv`, `rm` to include `-i` (interactive) mode.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**


<!-- BEGIN BEADS INTEGRATION v:1 profile:claude-rules-managed hash:managed-by-beads-md -->
## bd (beads) — Cross-Session Task Graph

This project is enrolled in bd for **dependency-graphed cross-session task tracking only**. bd does not replace TaskCreate (in-session todos), MEMORY.md (cross-project knowledge), or the `/checkpoint` recovery system. See `~/.claude/rules/beads.md` for the full boundary.

### Quick Reference

```bash
bd ready              # Find issues ready to work (no open blockers)
bd list --status=in_progress  # Currently claimed work
bd show <id>          # View issue + dependencies
bd update <id> --claim        # Claim work atomically
bd dep add <child> <parent>   # Add dependency edge
bd close <id>         # Mark complete
bd preflight          # Pre-PR check (lint, stale, orphans)
bd doctor             # Health check
```

### Rules

- Use bd for **cross-session task graphs with explicit dependencies**
- Use `TaskCreate` for in-conversation todos (bd does not replace it)
- Use `MEMORY.md` for cross-project knowledge (do not use `bd remember` for that)
- Never run `bd rules compact` — it would damage the modular `.claude/rules/*.md` architecture
- Never put credentials, PII, or anything that belongs in `~/.config/api-keys.env` into `bd remember`
- Run `bd prime` for the bd-side command reference

### Session Completion

For session end, follow the project's `/close` workflow (defined in `~/.claude/rules/session-workflow.md`). It already handles the project's push restrictions, security checks, and CHANGELOG updates. Do NOT follow bd's default "git push or not done" mandate — it does not understand the push-restriction rules in `~/.claude/rules/projects.md`.

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
