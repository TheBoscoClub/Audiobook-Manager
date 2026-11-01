# VM Lifecycle — Boot On Demand, Shut Down After — reference (NOT auto-loaded)

Detail moved out of `.claude/rules/vm-lifecycle.md`, same headings. Verbatim; the rule file keeps the binding text.

**Dev, QA and test VMs do not idle on Claude's account.** Boot for a specific job (deploy, test run, repro), shut down when it ends — idle VMs waste RAM/array I/O, and one holding credentials can make unwatched outbound API calls. A VM the operator started is the operator's to leave running, the QA VM included (decision 2026-11-01, bd `Audiobook-Manager-8vw`).

## The two properties that make it safe

1. **Only shuts down what it started.** `up` writes `~/.claude/cache/vm-started/<vm>`; `down`/`reap` refuse without it, so operator-started VMs are never touched (verified by removing the marker: `down` declined, VM kept running).

## Address lookup

Use `vm-session up` or `vm-session ip <vm>`. Never parse `virsh domifaddr` by hand: its default source returns guest **loopback** first, `--source arp` ages out, and a deploy aimed at `127.0.0.1` hits the host. The helper filters loopback/link-local and tries all three sources.
