# Maintenance Scheduling & Live Connections — Design Spec

**Date**: 2026-03-20
**Branch**: `maintenance-scheduling` (feature branch off `main`, rebased as hotfixes land)
**Status**: Design approved, spec review round 1 complete, pending implementation plan
**Spec Review**: Round 1 — 16 issues found and resolved. Round 2 — 4 new issues found and resolved. Spec clean.

---

## Overview

Three interconnected features for operational awareness and scheduled maintenance:

1. **Live Connections Dashboard** — Real-time count and list of connected users (Activity tab)
2. **Maintenance Scheduler** — Schedule one-time and recurring maintenance windows with automated execution (new "Maint Sched" tab)
3. **Maintenance Announcement System** — Pulsing indicator with expandable panel and Frankenstein knife switch dismissal

All three share a WebSocket backbone for real-time bidirectional communication.

---

## 1. Live Connections Dashboard

### Location

Top of the **Activity tab** in the back office (`utilities.html`, `data-section="activity"`).

### Display

- **Prominent count** of active connections at top of tab
- **Username list** of currently connected users
- **No activity snooping** — no display of what users are doing, what they're listening to, or device info
- WebSocket internally tracks streaming vs idle state for connection accuracy (false-positive/false-negative reduction), but this state is **not exposed in the UI**

### Data Source

Admin-only API endpoint backed by the WebSocket connection manager's shared state (see Section 4 for state management architecture).

---

## 2. Maintenance Scheduler

### Location

New **"Maint Sched" tab** in back office (7th tab, after System).

### Tab Contents

#### 2a. Maintenance Windows

- **Create window**: Form with fields for name, description, start time, duration, task type (from registry), lead time (default 48h), and schedule (one-time or recurring)
- **Schedule format**: Preset picker (daily, weekly, biweekly, monthly) with day/time selectors, plus an "Advanced" toggle revealing a raw cron expression input
- **Cron tooltip**: Hover/focus on the cron input shows: `"Min Hour Day-of-Mth Mth Day-of-Week (0=Sunday)"`
- **Window list**: Table of scheduled windows with status (upcoming, active, completed, cancelled), next run time, and controls (edit, cancel, delete)
- **Recurring management**: Edit or cancel future occurrences without affecting past records
- **Deletion**: Windows with execution history can be soft-deleted (status → "cancelled") but not hard-deleted. Windows with no history can be hard-deleted.

#### 2b. Manual Announcements

- **Free-form text input** for immediate maintenance messages
- **Active messages list** with dismiss/delete controls
- Admin can permanently dismiss manual messages (removes from all clients)
- Manual messages push to connected clients immediately via WebSocket

#### 2c. Execution Status

- **History view**: Past maintenance windows with execution results (success, failure, skipped)
- **Active execution**: Progress indicator when a maintenance task is running

### Task Registry (Plugin Architecture)

Maintenance task types use a **registry/plugin pattern** for easy extensibility:

```python
# Each task type is a registered handler
@maintenance_registry.register
class DatabaseVacuumTask(MaintenanceTask):
    name = "db_vacuum"
    display_name = "Database Vacuum & Optimize"
    description = "Run VACUUM and ANALYZE on all databases"

    def validate(self, params: dict) -> ValidationResult:
        """Pre-flight checks before execution — called at creation AND before execution"""
        ...

    def execute(self, params: dict, progress_callback) -> ExecutionResult:
        """Perform the maintenance task"""
        ...
```

**Interface per task handler**:

| Method | Purpose |
|--------|---------|
| `name` | Unique identifier (used in DB, API) |
| `display_name` | Human-readable name for UI |
| `description` | Shown in scheduler form |
| `validate(params)` | Pre-flight check — called at **window creation** (catch bad params early) and again at **execution time** (catch stale state) |
| `execute(params, progress_callback)` | Perform the work, report progress |
| `estimate_duration()` | Optional: estimated time for UI display |

**Initial task handlers** (shipped with feature):

| Handler | Purpose |
|---------|---------|
| `db_vacuum` | VACUUM and ANALYZE on library DB |
| `db_integrity` | Integrity check on all databases |
| `db_backup` | Database backup to configured location |
| `library_scan` | Scan for new/changed audiobook files |
| `hash_verify` | Verify file hashes against database |

**Adding a new task type**: Write a single module implementing `MaintenanceTask`, register it. No scheduler core changes needed. Removing: unregister. The scheduler invokes handlers by name from the registry — it never knows or cares what they do.

### Execution Architecture

The scheduler runs announce-first in implementation sequence, but **both announce and execute must be complete before the branch merges to main**. This is a hard gate — no partial-feature merge.

**Execution flow**:

1. Scheduler service checks for upcoming windows on its timer cycle
2. At configurable lead time (default T-48h): Banner announcement pushed to all connected clients
3. At scheduled time: Validate task → Execute task → Record result
4. On completion: Update window status, clear announcement if no more upcoming
5. On failure: Record error, push failure notification to admin connections

**Concurrency**: Only one maintenance task executes at a time. If a window's scheduled time arrives while another task is running, it queues and starts after the current task completes. A file-based lock (`/var/lib/audiobooks/.run/maintenance.lock`) prevents concurrent execution even across process restarts.

### Scheduler Daemon Architecture

The scheduler runs as a **separate systemd service** (`audiobook-scheduler.service`), not as a background thread inside the Flask process. This avoids:

- GIL contention with request handling during long-running tasks (e.g., `hash_verify`)
- Split-brain in multi-worker deployments
- Scheduler death being invisible if the Flask process crashes independently

**Design**:

- Standalone Python process that imports the task registry and DB helpers
- Polls `maintenance_windows` table every 60 seconds for windows where `next_run_at <= now`
- Communicates execution status to the Flask API via the shared database (not in-memory)
- Pushes WebSocket notifications by writing to a notification queue table that the WebSocket handler polls or watches
- Added to `audiobook.target` as `Wants=audiobook-scheduler.service`

---

## 3. Maintenance Announcement System

### Indicator (Desktop & Mobile — Unified)

- **Visual**: Small red circle with white "!" (exclamation mark), positioned in a fixed corner of the viewport
- **Animation**: Slow, ominous pulse — the entire indicator scales slightly (1.0 → 1.15 → 1.0) and the glow intensifies rhythmically. Cycle time ~2 seconds.
- **Color**: Neon red (`#FF0040` or similar high-saturation red) with dark red drop shadow for 3D depth effect
- **Behavior**: Click/tap to expand message panel. Indicator visible on all pages (library, player, back office) when an active announcement exists.
- **Desktop and mobile identical** — same pulsing "!" everywhere, not a full-width bar on desktop

### Expanded Message Panel

- **Trigger**: Click/tap the pulsing indicator
- **Content**: All active messages (manual + scheduled) displayed with newline separation
- **Dynamic resize**: Panel accommodates 1–4 lines without overlapping or obscuring page content (buttons, grids, text)
- **Overflow**: If messages exceed 4 lines, panel shows first 4 lines with a scrollable overflow area and "N more messages" indicator at the bottom
- **Dismiss**: Collapse panel by clicking outside it (focus loss)
- **Close/Off**: Frankenstein knife switch inside the panel (see below)

### Frankenstein Knife Switch (Dismiss Control)

**Visual Design**:

- Vertical copper blade pivoting into jaw contacts
- Red bakelite handle
- Two positions: UP = banner on (circuit closed), DOWN = banner off (circuit open)

**Animation** (~0.6–0.8 seconds total throw):

- Blade pivots through an arc between jaw positions
- Fast start, slight deceleration at end of travel, subtle bounce at stop position
- **Bzzzt sound**: Fires during arc travel (~0.1s into motion). Synthesized via Web Audio API — short burst of bandpass-filtered noise (50–100ms) with electrical arc character. ON throw gets slightly longer arc buzz, OFF throw gets a sharper snap.
- **Clunk sound**: Fires at stop position. Low-frequency oscillator pulse with fast decay — heavy mechanical contact sound. Different tonal weight for ON vs OFF (closing a circuit sounds different from breaking one).
- Both sounds synthesized on the fly via Web Audio API — no audio asset files.

**Accessibility**:

- `role="switch"` with `aria-checked="true/false"` reflecting on/off state
- Keyboard operable: `Enter` or `Space` toggles the switch
- `title` tooltip: "Dismiss maintenance announcements for this session"
- Focus outline visible when keyboard-navigated

**Behavior**:

- Throwing the switch to OFF dismisses the banner for the **current session only** (session-scoped, not permanent for scheduled announcements)
- Admin can permanently dismiss **manual messages** from the Maint Sched tab (removes for all clients)
- Banner reappears on next session/page load if announcements still active

### Banner Timing

| Source | Appears | Dismissal |
|--------|---------|-----------|
| **Scheduled maintenance** | Configurable lead time before window start (default: **48 hours**) | Session-scoped (knife switch). Reappears next session. |
| **Manual admin message** | Immediately on all connected clients | Session-scoped for users (knife switch). Admin can permanently delete from Maint Sched tab. |

**Edge case**: If a window is scheduled less than its lead time from now (e.g., 6 hours out with a 48-hour lead), the announcement appears immediately upon window creation.

### Content Rules

- Manual and scheduled messages **coexist** with newline separation in the expanded panel
- Panel dynamically resizes from 1 to 4 lines (scrollable overflow beyond 4)
- Messages ordered: manual first (urgent), then scheduled by time
- No content overlap or obscuring of underlying page elements

### Mobile Specifics

- Same pulsing "!" indicator as desktop (no separate mobile treatment)
- Tap to expand popup with full message and knife switch
- Collapse on focus loss (tap outside popup)
- Popup sized appropriately for mobile viewport

---

## 4. WebSocket Architecture

### Server Infrastructure — CRITICAL PREREQUISITE

**The current serving stack (Waitress WSGI + proxy_server.py) does not support WebSocket.** This must be resolved as the first implementation step.

**Problem**: Waitress is a pure-WSGI server with no WebSocket protocol support. WSGI is synchronous request/response — it cannot handle the HTTP/1.1 `Upgrade: websocket` handshake or maintain persistent framed connections. Additionally, `proxy_server.py` strips hop-by-hop headers (`Upgrade`, `Connection`) and uses `urllib.request` for API forwarding, which cannot tunnel WebSocket connections.

**Solution**: Replace Waitress with **Gunicorn + gevent worker** for the API service:

| Component | Current | After |
|-----------|---------|-------|
| API server | Waitress (WSGI) | Gunicorn + geventwebsocket worker (WSGI + WebSocket) |
| Proxy | `proxy_server.py` (Python HTTP server) | `proxy_server.py` extended with WebSocket tunneling OR replaced with Caddy reverse proxy |
| systemd ExecStart | `waitress-serve ...` | `gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 ...` |
| Dependencies | `waitress` | `gunicorn`, `gevent`, `gevent-websocket`, `flask-sock` |

**Why geventwebsocket worker**: `flask-sock` requires the `geventwebsocket` worker class — not the plain `-k gevent` worker — to properly handle the WebSocket upgrade handshake. Using plain `-k gevent` causes WebSocket connections to silently fail with a normal HTTP response instead of the `101 Switching Protocols` upgrade.

**Why single worker (`-w 1`)**: The connection manager uses in-memory state. Multiple workers would each see a subset of connections. Single gevent worker handles concurrency via cooperative greenlets, not OS threads/processes. This matches the current deployment model (single Waitress process). If horizontal scaling is ever needed, connection state moves to the database (a future concern, not this feature branch).

**HARD CONSTRAINT**: The systemd unit file must include a comment noting that `-w 1` is required for connection manager correctness — do not increase without migrating state to the database.

**gevent monkey-patching**: The application entry point must call `gevent.monkey.patch_all()` before any other imports. This patches stdlib I/O (including `sqlite3`) to yield cooperatively to the gevent hub during blocking operations. Without monkey-patching, SQLite queries block the entire greenlet loop, stalling all concurrent WebSocket connections.

**Proxy decision**: Extending `proxy_server.py` to detect `Upgrade: websocket` headers and tunnel the raw TCP connection is feasible but fragile. If Caddy or nginx is already in the deployment path, routing WebSocket through the real reverse proxy is preferable. The implementation plan will evaluate both options and pick the simpler one.

### Endpoint

- **URL**: `/api/ws` (under the existing `/api/` prefix so the proxy forwards it)
- **Protocol**: Standard WebSocket (RFC 6455)
- **Auth**: Session cookie validated on connection upgrade. If `AUTH_ENABLED=false`, unauthenticated clients are allowed (announcements are public-facing). If `AUTH_ENABLED=true`, `get_current_user()` is called before accepting the WebSocket; unauthenticated attempts receive HTTP 401 before upgrade.

### Server-Side

- **Library**: `flask-sock` (lightweight, works with Gunicorn+geventwebsocket worker)
- **Connection manager**: In-memory dict of active connections keyed by session ID. Single-worker deployment means all connections are visible to the one process.
- **Heartbeat**: Server expects client ping every 10 seconds; connection considered stale after 30 seconds of silence
- **Cleanup**: Stale connections removed from tracking on missed heartbeat or disconnect event
- **Scheduler notifications**: The scheduler service (separate process) writes to a `maintenance_notifications` queue table in the DB. The WebSocket handler polls this table every 5 seconds (via a gevent greenlet that yields cooperatively thanks to monkey-patching) and pushes new notifications to connected clients, then marks them as delivered. The 0-5 second delivery lag is acceptable for maintenance announcements.
  For immediate responsiveness on manual message creation, the admin POST endpoint can also directly broadcast via the in-process connection manager (no DB round-trip needed for admin-initiated pushes).

### Client-Side

- **Native WebSocket API** (vanilla JS, no library)
- **Auto-reconnect**: Exponential backoff on disconnect (1s, 2s, 4s, 8s, max 30s)
- **Heartbeat**: Client sends ping message every 10 seconds with connection state
- **Graceful degradation**: If WebSocket fails to connect after 3 attempts, fall back to polling `GET /api/maintenance/announcements` every 30 seconds (see public endpoint below)

### Message Types

**Client → Server**:

| Type | Payload | Purpose |
|------|---------|---------|
| `heartbeat` | `{ state: "idle" \| "streaming" \| "paused" }` | Connection liveness + activity state |

**Server → Client**:

| Type | Payload | Purpose |
|------|---------|---------|
| `maintenance_announce` | `{ messages: [...], windows: [...] }` | Push announcement to all clients |
| `maintenance_dismiss` | `{ message_id: ... }` | Admin permanently dismissed a manual message |
| `maintenance_update` | `{ window_id: ..., status: ... }` | Window status change (started, completed, failed) |

### API Endpoints (New)

**Admin endpoints** — use `@admin_if_enabled` (not `@admin_required`) to support both `AUTH_ENABLED=true` and `AUTH_ENABLED=false` deployment modes:

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/admin/connections` | GET | `@admin_if_enabled` | List active connections (count + usernames) |
| `/api/admin/maintenance/windows` | GET | `@admin_if_enabled` | List all maintenance windows |
| `/api/admin/maintenance/windows` | POST | `@admin_if_enabled` | Create maintenance window (validates task params) |
| `/api/admin/maintenance/windows/<id>` | PUT | `@admin_if_enabled` | Update window |
| `/api/admin/maintenance/windows/<id>` | DELETE | `@admin_if_enabled` | Delete window (soft-delete if has history) |
| `/api/admin/maintenance/messages` | GET | `@admin_if_enabled` | List manual messages |
| `/api/admin/maintenance/messages` | POST | `@admin_if_enabled` | Create manual message (pushes immediately) |
| `/api/admin/maintenance/messages/<id>` | DELETE | `@admin_if_enabled` | Permanently dismiss manual message |
| `/api/admin/maintenance/tasks` | GET | `@admin_if_enabled` | List registered task types from registry |
| `/api/admin/maintenance/history` | GET | `@admin_if_enabled` | Execution history |

**Public endpoint** — polling fallback for clients without WebSocket, and pre-login announcement visibility:

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/maintenance/announcements` | GET | `@guest_allowed` | Active announcements (manual messages + upcoming windows within lead time) |

**Why `@guest_allowed`**: Maintenance announcements are public-facing information — they tell users the server is going down. This must be visible even to unauthenticated users (pre-login, expired session). `@guest_allowed` always allows the request through, populating `g.user` if a session exists (for future per-user logic) but never returning 401.

---

## 5. Database Schema (New Tables in audiobooks.db)

**Timezone convention**: All datetime columns store **UTC** (via SQLite `datetime('now')`). Cron expressions are interpreted in the server's local timezone using `croniter` with explicit `tzinfo` from the system locale. The UI converts UTC timestamps to the user's local timezone via JavaScript `Intl.DateTimeFormat`.

```sql
-- Maintenance windows (scheduled or one-time)
CREATE TABLE IF NOT EXISTS maintenance_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    task_type TEXT NOT NULL,              -- Registry handler name (e.g., "db_vacuum")
    task_params TEXT DEFAULT '{}',        -- JSON parameters for the handler
    schedule_type TEXT NOT NULL,          -- "once" or "recurring"
    cron_expression TEXT,                 -- For recurring: cron string (NULL for one-time)
    scheduled_at TEXT,                    -- For one-time: ISO 8601 datetime (UTC)
    next_run_at TEXT,                     -- Computed: next execution time (UTC)
    duration_minutes INTEGER DEFAULT 30,
    lead_time_hours INTEGER DEFAULT 48,   -- How many hours before to show announcement
    status TEXT DEFAULT 'active',         -- "active", "paused", "cancelled", "completed"
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Trigger to auto-update updated_at on modification
CREATE TRIGGER IF NOT EXISTS trg_maint_windows_updated
    AFTER UPDATE ON maintenance_windows
    FOR EACH ROW
BEGIN
    UPDATE maintenance_windows SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- Manual announcement messages
CREATE TABLE IF NOT EXISTS maintenance_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    created_by TEXT NOT NULL,             -- Admin username
    created_at TEXT DEFAULT (datetime('now')),
    dismissed_at TEXT,                    -- NULL = active, timestamp = permanently dismissed
    dismissed_by TEXT                     -- Admin who dismissed
);

-- Execution history
CREATE TABLE IF NOT EXISTS maintenance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,                 -- "running", "success", "failure", "cancelled"
    result_message TEXT,                  -- Human-readable result or error
    result_data TEXT DEFAULT '{}',        -- JSON execution details
    FOREIGN KEY (window_id) REFERENCES maintenance_windows(id) ON DELETE CASCADE
);

-- Notification queue (scheduler → WebSocket handler)
CREATE TABLE IF NOT EXISTS maintenance_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type TEXT NOT NULL,      -- "announce", "dismiss", "update"
    payload TEXT NOT NULL,                -- JSON notification data
    created_at TEXT DEFAULT (datetime('now')),
    delivered INTEGER DEFAULT 0           -- 0 = pending, 1 = delivered to WebSocket clients
);

CREATE INDEX IF NOT EXISTS idx_maint_windows_next_run ON maintenance_windows(next_run_at);
CREATE INDEX IF NOT EXISTS idx_maint_windows_status ON maintenance_windows(status);
CREATE INDEX IF NOT EXISTS idx_maint_messages_active ON maintenance_messages(dismissed_at);
CREATE INDEX IF NOT EXISTS idx_maint_history_window ON maintenance_history(window_id);
CREATE INDEX IF NOT EXISTS idx_maint_notifications_pending ON maintenance_notifications(delivered, created_at);
```

---

## 6. File Changes Summary

### New Files

| File | Purpose |
|------|---------|
| `library/backend/api_modular/maintenance.py` | Flask blueprint: maintenance API endpoints |
| `library/backend/api_modular/websocket.py` | WebSocket endpoint + connection manager |
| `library/backend/api_modular/maintenance_tasks/__init__.py` | Task registry |
| `library/backend/api_modular/maintenance_tasks/db_vacuum.py` | Database vacuum handler |
| `library/backend/api_modular/maintenance_tasks/db_integrity.py` | Integrity check handler |
| `library/backend/api_modular/maintenance_tasks/db_backup.py` | Database backup handler |
| `library/backend/api_modular/maintenance_tasks/library_scan.py` | Library scan handler |
| `library/backend/api_modular/maintenance_tasks/hash_verify.py` | Hash verification handler |
| `library/backend/maintenance_scheduler.py` | Standalone scheduler daemon |
| `library/web-v2/js/websocket.js` | Client-side WebSocket (heartbeat, message handling, auto-reconnect, polling fallback) |
| `library/web-v2/js/maintenance-banner.js` | Pulsing indicator, expanded panel, knife switch, Web Audio sounds |
| `library/web-v2/js/maint-sched.js` | Maint Sched tab UI logic |
| `library/web-v2/css/maintenance-banner.css` | Banner indicator, panel, knife switch styling |
| `systemd/audiobook-scheduler.service` | Systemd service for maintenance scheduler |

### Modified Files

| File | Change |
|------|--------|
| `library/backend/schema.sql` | Add 4 new tables + indices + trigger |
| `library/backend/api_modular/__init__.py` | Register maintenance blueprint, init WebSocket endpoint, add `gevent.monkey.patch_all()` at entry point |
| `library/web-v2/utilities.html` | Add "Maint Sched" tab button + section content |
| `library/web-v2/utilities.js` | Init maint-sched section, integrate connection count into Activity tab |
| `library/web-v2/css/utilities.css` | Styles for Maint Sched tab content |
| `library/web-v2/shell.html` | Include websocket.js and maintenance-banner.js scripts |
| `library/web-v2/css/shell.css` | Import maintenance-banner.css |
| `library/web-v2/proxy_server.py` | WebSocket upgrade tunneling (or replaced with Caddy config) |
| `requirements.txt` | Add `gunicorn`, `gevent`, `gevent-websocket`, `flask-sock`, `croniter`; remove `waitress` |
| `library/requirements-docker.txt` | Same dependency changes as requirements.txt |
| `systemd/audiobook-api.service` | Update ExecStart from waitress-serve to gunicorn |
| `systemd/audiobook.target` | Add `Wants=audiobook-scheduler.service` |
| `install.sh` | Include new files, scheduler service, updated dependencies |
| `Dockerfile` | Include new files, updated dependencies |
| `docs/ARCHITECTURE.md` | Document WebSocket, maintenance system, Gunicorn migration |
| `docs/TROUBLESHOOTING.md` | Maintenance troubleshooting section |
| `README.md` | Feature description update |
| `CHANGELOG.md` | Feature entry |

---

## 7. CSS Specifications

### Pulsing Indicator

```css
.maintenance-indicator {
    position: fixed;
    /* Corner position TBD during implementation */
    z-index: 9999;
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    background: #FF0040;
    color: white;
    font-weight: bold;
    font-size: 1.1rem;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 0 8px rgba(255, 0, 64, 0.6),
                0 0 16px rgba(255, 0, 64, 0.3);
    animation: maintenance-pulse 2s ease-in-out infinite;
}

@keyframes maintenance-pulse {
    0%, 100% { transform: scale(1.0); box-shadow: 0 0 8px rgba(255,0,64,0.6); }
    50%      { transform: scale(1.15); box-shadow: 0 0 20px rgba(255,0,64,0.9); }
}
```

### Expanded Panel Message Text

- **Font size**: 0.95rem (matches marquee `.marquee-item`)
- **Color**: Neon red (#FF0040) with dark red drop shadow (`text-shadow: 2px 2px 0 #8B0000, 4px 4px 8px rgba(139,0,0,0.5)`) for 3D depth
- **Animation**: Slow pulse on text opacity (1.0 → 0.85 → 1.0, ~3s cycle) — not the same as indicator pulse
- **Background**: Charcoal gradient matching marquee (#141414 → #080808)
- **Borders**: Match marquee (1px gold-dark top, 2px gold bottom)
- **Overflow**: `max-height` set to 4 lines (~5.6rem), `overflow-y: auto` for scrollable content beyond

### Knife Switch

- Rendered as SVG or CSS-drawn element inside the expanded panel
- Copper blade: `#B87333` (copper) with metallic gradient
- Red bakelite handle: `#8B1A1A` with subtle sheen
- Jaw contacts: `#B8860B` (brass-dark)
- Pivot point: centered, blade rotates ±45° from vertical
- `role="switch"`, `aria-checked`, keyboard-operable (`Enter`/`Space`), `title` tooltip
- Focus outline: `2px solid var(--brass-light)` when `:focus-visible`

---

## 8. Non-Functional Requirements

- **Performance**: WebSocket adds minimal overhead — one connection per client, 10-second heartbeat is lightweight. Single geventwebsocket worker handles concurrent connections cooperatively via monkey-patched I/O.
- **Graceful degradation**: If WebSocket fails to connect after 3 attempts, client falls back to polling `GET /api/maintenance/announcements` every 30 seconds. This public endpoint returns the same announcement data the WebSocket would push.
- **Security**: All admin endpoints behind `@admin_if_enabled` (works in both auth modes). WebSocket validates session on upgrade (401 before upgrade if auth required and missing). No user activity data exposed in UI.
- **Backward compatibility**: Clients without WebSocket support still see announcements via polling fallback endpoint.
- **Timezone handling**: DB stores UTC. Cron expressions interpreted via `croniter` with server-local `tzinfo`. UI converts to user-local time via JS `Intl.DateTimeFormat`.
- **Testing**: Unit tests for scheduler logic, task registry, cron parsing, and API endpoints. Integration tests for WebSocket on test VM. Playwright tests for banner UI, knife switch interaction, and keyboard accessibility. Gunicorn migration tested end-to-end before other features.
