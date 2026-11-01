# Upgrade System Consistency — Mandatory Cross-File Review — reference (NOT auto-loaded)

Detail moved out of `.claude/rules/upgrade-consistency.md`, same headings. Verbatim; the rule file keeps the binding text.

## New-Script Wiring Enforcement (MANDATORY — added 2026-04-17)

**Why**: v8.3.0/v8.3.1 shipped `scripts/stream-translate-worker.py` with zero wiring; `streaming_segments` rows had no reader and the prod Chinese translation demo hung on a 5-minute "排队中" spinner while CHANGELOG claimed it shipped. Post-mortem: memory `feedback_upgrade_consistency_enforcement.md`.
