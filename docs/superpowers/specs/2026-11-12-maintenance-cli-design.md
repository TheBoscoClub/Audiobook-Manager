# Maintenance from the command line — design

**Status, plan and decisions: bd `Audiobook-Manager-bd38.1`**

## Why

A host that runs The Library sometimes has to restart. A restart while someone is listening is an outage for
that person. Today the two things that would prevent it exist only behind an admin login in a browser: seeing who
is connected, and warning users. A script that restarts the host unattended can use neither.

This adds one command a root script on the same host can call: ask who is there, warn everyone, and schedule a
downtime notice ahead of time.

## Goals

- A script can ask "is anyone connected, is anyone listening" and act on the exit code alone.
- The answer fails closed: when the app is running and the command cannot be sure, it never says "nobody".
- An app that is not running has no listeners, so it does not hold up a restart.
- A script can post a notice to all users now, and remove it.
- A script can schedule a host-downtime window that announces itself ahead of time and runs nothing.
- The caller needs no browser, no user account and no entry in any credential store.

## Not goals

- The app does not restart the host or decide whether a restart may happen. The caller decides.
- No email or other out-of-band notice. Notices are the in-app banner only.
- No change to what an admin can already do in the browser, apart from the new window type appearing there.
- Nothing on the Cloudflare side (bd `Audiobook-Manager-bd38.2`).

## What the design must prevent

These are outcomes that would make the work a failure even if the command runs. The design below is built to
rule each one out; none of them is true of it.

- It reports "nobody" while a listener is connected, or while the app is running but did not answer.
- The local door can be reached from the network, or used by a local user who is neither root nor the service
  user. Prevented by three independent locks (see "The local door").
- A host-downtime window sets off one of The Library's own maintenance tasks (database vacuum, library rescan,
  backup and the like). Today every window runs such a task at its start time; this one must run none.

## How the parts work today

- Each browser holds a WebSocket to the API and reports its state every 10 seconds: `browsing`, `connected`
  (player open, paused) or `listening`. The API keeps this in the memory of its single worker and drops a
  connection after 15 seconds of silence. It is readable only through `GET /api/admin/connections`, admin session
  required.
- A browser whose WebSocket fails three times falls back to polling the public announcements endpoint every
  30 seconds. It is then invisible in the connection list.
- An announcement is a row in `maintenance_messages`, pushed to every connected browser and shown to anyone who
  loads the site until dismissed.
- A maintenance window is a row in `maintenance_windows`. The scheduler daemon announces it once the lead time is
  reached and runs its `task_type` from the task registry at the start time. Every window must name a task.
- The API service gets a runtime directory from systemd, recreated at every start, already used for the
  cache-purge token.

## Approaches

1. **Run-directory token and a local-only door (chosen by the operator).** The API writes a random token into its
   runtime directory at every start, readable by the service user and root only. A small set of API paths accepts
   only that token. Narrow: the token can do the three things above and nothing else. Nothing to store or rotate.
2. **Log in as an existing admin account.** Rejected as a permanent method. It was raised only as a stopgap
   until this command exists: the account's secret is a full admin login, would have to be kept where a root
   script can reach it, and every check leaves an admin session behind. Nothing is built for it.
3. **Read the database from the command.** Cannot work for live use: the connection state is not in the
   database. It would also break the rule that nothing outside the app touches its databases.

## Design

### The token

- At start the API creates `maintenance-token` in the app's configured run directory (`AUDIOBOOKS_RUN_DIR`, the
  directory its lock files already use): 32 random bytes, hex, mode `0400`, owned by the service user. A new value
  at every start; the old one stops working.
- The path comes from configuration (`AUDIOBOOKS_MAINTENANCE_TOKEN_FILE` in `lib/audiobook-config.sh` and
  `library/config.py`), never a literal.
- Only the real server entry point writes it. Building an app in a test or a tool never does.
- The token is compared in constant time. It is never logged and never returned by any endpoint.

### The local door

New paths under `/api/local/maintenance/`. Each requires both:

- the token in an `Authorization: Bearer` header, and
- a loopback peer address, and
- no forwarding header. The proxy adds one to everything it relays, so a relayed request is refused here even if
  the proxy's own refusal were bypassed.

The public-facing proxy refuses to forward `/api/local/` (answers 404), so the paths are unreachable through the
front door even with a token. The session-cookie login is not accepted on these paths, and the token is not
accepted anywhere else.

| Path | Does |
| --- | --- |
| `GET  status` | who is connected and in which state |
| `POST announce` | create an announcement, push it now |
| `POST clear` | dismiss one announcement by id, or every announcement this command created |
| `POST window` | create a host-downtime window |
| `POST window/cancel` | cancel a host-downtime window by id |

Announcements and windows created here are recorded as created by `maintenance-cli`, so `clear` and `cancel`
never touch what an admin created in the browser.

### Counting who is there

- WebSocket connections are counted as today.
- A logged-in browser that has fallen back to polling is counted too: the announcements endpoint records the
  time of each poll per session, and a session that polled within the last 60 seconds is reported with state
  `polling`.
- `polling` is treated as listening for the exit code, because the app cannot see that browser's player.
- Two tabs of one session still show as one entry, in the state the newer tab reports.

### The host-downtime window

- A new registry task, `host_downtime`, that does nothing and reports success. It fits the existing table,
  scheduler, lead-time announcement and the browser's window list without a schema change.
- The announcement banner shows the window's name, message and start time from the lead time until the start
  time. At the start time the scheduler marks the window completed, as it does for any one-time window.
- If the restart is put off, the caller cancels the window and creates a new one. For the minutes around the
  restart itself the caller uses `announce`.
- Lead time stays in whole hours, as the table has it. A window that starts sooner than its lead time is
  announced at once.

### The command

`audiobook-maintenance`, a wrapper in the scripts directory like the others. It runs as root or as the service
user; anyone else gets a clear refusal. Root steps down to the service user before any application code runs, so
nothing the service user could alter ever executes as root. It talks only to the API on loopback.

```text
audiobook-maintenance status
audiobook-maintenance announce "The Library restarts at 03:00 and is back in about five minutes"
audiobook-maintenance clear [ID]
audiobook-maintenance window add --start 2031-01-25T03:00 --minutes 10 --lead-hours 24 --message "..."
audiobook-maintenance window cancel ID
```

`status` prints one summary line and one line per connection:

```text
connected=2 listening=1 polling=0
alice listening
bob connected
```

Exit codes of `status`:

| Code | Meaning | A restart script should |
| --- | --- | --- |
| 0 | nobody connected, or the app is not running | go ahead |
| 10 | someone connected, nobody listening | decide by its own rule |
| 20 | someone listening, or polling | not restart |
| 30, or any other | unknown: the app is running but gave no usable answer (timeout, token unreadable, bad answer) | not restart |

Zero is the only "nobody". A crash of the command itself therefore also reads as unknown.

**App not running.** The audio a listener plays is served by the API process itself, so with that process gone
nobody can be streaming. The command says so only on positive evidence: systemd reports the API service as
inactive or failed, and the connection to its port is refused. It then prints `app=down connected=0` and exits 0.
A service that is starting, stopping, or running but silent is not "down"; that is unknown.

**App just started.** The connection list is in the API's memory and starts empty; browsers reconnect over the
next seconds. "Nobody" from an app that started less than 90 seconds ago is therefore reported as unknown.

The other verbs exit 0 on success and print the id they created; any failure is non-zero with the reason on
standard error.

## Limits that remain

- A paused player stays `connected` for as long as its tab is open.
- A buffering stream counts as `listening`.
- A listener who is not logged in to a browser session the app can see (for example audio already buffered in a
  closed tab) cannot be detected.
- The answer is up to about 15 seconds old for WebSocket clients and 60 seconds for polling ones.

## Wiring and proof

- `scripts/install-manifest.sh`, `install.sh`, `upgrade.sh`, `scripts/audiobook-help`: the new wrapper and its
  link.
- `docs/ARCHITECTURE.md`, `README.md`, `CHANGELOG.md`: the command, the local door, the new task.
- Unit tests on the dev machine: token check (wrong token, missing token, non-loopback peer, cookie on a local
  path, token on a normal path), exit-code mapping, the no-op task, `clear` and `cancel` leaving admin-created
  rows alone.
- On the test VM: the proxy answers 404 for `/api/local/`; `status` goes 0 → 10 → 20 as a browser connects and
  plays; `status` prints `app=down` and exits 0 with the API service stopped, and exits with an unknown code
  when the service is running but its port is blocked; a window announces at its lead time and runs no
  task; the token file's mode and owner.

## Left to the builder

- Names of the configuration variable and the Python module.
- Whether the poll-time record is in memory or a table (memory is enough: it is lost with the same restart that
  loses the connection list).
- JSON shapes of the local endpoints.
