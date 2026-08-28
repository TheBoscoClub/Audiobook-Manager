# VM Lifecycle — Boot On Demand, Shut Down After

**The dev, QA and test VMs do not idle.** They are booted for a specific piece
of work — a deploy, a test run, a reproduction — and shut down when that work
finishes. A VM left running consumes RAM and array I/O for nothing, and one
holding credentials in its config can make outbound API calls nobody is
watching.

## Never start a VM with a bare `virsh start`

Use the helper, which records what it started so the shutdown is automatic
rather than remembered:

```bash
IP=$(vm-session up dev-audiobook-cachyos)     # start, wait for SSH, print the IP
./upgrade.sh --from-project "$PWD" --remote "$IP" --user claude --yes
vm-session down dev-audiobook-cachyos          # shut down
```

`vm-session` lives at `~/.claude/bin/vm-session` and is host-wide, not
project-specific — any project gets the same guarantee.

## The two properties that make it safe

1. **It only shuts down what it started.** `up` writes a marker to
   `~/.claude/cache/vm-started/<vm>`; `down` and `reap` refuse to act without
   one. A VM the operator brought up by hand is never touched — verified by
   removing the marker and watching `down` decline while the VM kept running.
2. **Forgetting is not fatal.** The `SessionEnd` hook runs `vm-session reap`,
   which shuts down every marked VM still running. A session that dies mid-way
   leaves the markers behind, and the next `reap` clears them.

## Address lookup

Take the IP from `vm-session up` or `vm-session ip <vm>`. Do not parse
`virsh domifaddr` by hand: its default source returns the guest's **loopback**
first for these VMs, and `--source arp` entries age out. Pointing a remote
deploy at `127.0.0.1` aims it at the host. The helper filters loopback and
link-local and tries all three sources.

## Exceptions

`test-audiobook-cachyos` is governed by `testing.md` — `/test` owns its
lifecycle and must always shut it down, per `post_test_restore: true` in
`~/.claude/config/project-vm-map.json`. Nothing here overrides that.
