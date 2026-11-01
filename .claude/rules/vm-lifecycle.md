# VM Lifecycle — Boot On Demand, Shut Down After

Detail, incidents and reasoning per section: `.claude/docs/rules-reference/vm-lifecycle.md` (same headings).

**Dev, QA and test VMs do not idle.** Boot for a specific job (deploy, test run, repro), shut down when it ends.

## Never start a VM with a bare `virsh start`

```bash
IP=$(vm-session up dev-audiobook-cachyos)     # start, wait for SSH, print the IP
./upgrade.sh --from-project "$PWD" --remote "$IP" --user claude --yes
vm-session down dev-audiobook-cachyos          # shut down
```

`~/.claude/bin/vm-session` is host-wide, not project-specific.

## The two properties that make it safe

1. **Only shuts down what it started.** `up` writes `~/.claude/cache/vm-started/<vm>`; `down`/`reap` refuse without it, so operator-started VMs are never touched.
2. **Forgetting is not fatal.** `SessionEnd` hook runs `vm-session reap`, shutting down every marked VM still running; markers left by a dead session are cleared by the next `reap`.

## Address lookup

Use `vm-session up` or `vm-session ip <vm>`. Never parse `virsh domifaddr` by hand: its default source returns guest **loopback** first, so a deploy can hit the host.

## Exceptions

`test-audiobook-cachyos` is governed by `testing.md`: `/test` owns its lifecycle and must always shut it down (`post_test_restore: true` in `~/.claude/config/project-vm-map.json`). Nothing here overrides that.
