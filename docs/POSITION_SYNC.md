# Per-User Position Tracking

Local-only playback position tracking with per-user isolation when authentication is enabled.

## Table of Contents

1. [Overview](#overview)
2. [How It Works](#how-it-works)
3. [Multi-User Support](#multi-user-support)
4. [Auth Disabled Fallback](#auth-disabled-fallback)
5. [Listening History](#listening-history)
6. [Web Player Integration](#web-player-integration)
7. [API Reference](#api-reference)
8. [Database Schema](#database-schema)
9. [Troubleshooting](#troubleshooting)
10. [History](#history)

---

## Overview

Position tracking allows you to pause an audiobook and resume at the exact same position later. All position data is stored locally -- there is no external cloud synchronization.

### Key Features

- **Per-user tracking**: Each authenticated user has independent playback positions
- **Encrypted storage**: Per-user positions stored in the encrypted auth database (SQLCipher)
- **Automatic listening history**: Every position save creates a listening session record
- **Dual-layer persistence**: localStorage (fast cache) + API (persistent)
- **Automatic player saves**: Web player saves positions every 15 seconds during playback
- **Auth-disabled fallback**: Single-user global position in the library database when auth is off

---

## How It Works

### System Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                   POSITION TRACKING ARCHITECTURE                            │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────┐         ┌───────────────────┐
│   Web Browser    │         │  Audiobook-Manager │
│   (Player)       │         │       API          │
├──────────────────┤         ├───────────────────┤
│                  │  Every  │                   │
│  localStorage ───┼──15s───▶│  Auth Database    │  (per-user, encrypted)
│  (fast cache)    │  save   │     OR            │
│                  │         │  Library Database │  (global, auth disabled)
│  PlaybackManager │         │                   │
│  class           │         │  position_sync.py │
│                  │         │  Flask Blueprint  │
└──────────────────┘         └───────────────────┘
```

### Position Save Flow

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       POSITION SAVE FLOW                                    │
└─────────────────────────────────────────────────────────────────────────────┘

  Web Player                   Flask API
      │                            │
      │  Every 15s during play     │
      ├───────────────────────────▶│
      │  PUT /api/position/<id>    │
      │  {position_ms: 3600000}    │
      │                            │
      │                  ┌─────────┴──────────┐
      │                  │  Auth enabled?      │
      │                  └────┬──────────┬─────┘
      │                       │          │
      │               YES     │          │  NO
      │                       ▼          ▼
      │               ┌────────────┐  ┌────────────┐
      │               │ Auth DB    │  │ Library DB │
      │               │ Per-user   │  │ Global     │
      │               │ (encrypted)│  │ position   │
      │               └────────────┘  └────────────┘
      │                       │          │
      │                       │          │
      │               ┌───────┘          │
      │               │ (auth only)      │
      │               ▼                  │
      │        ┌──────────────┐          │
      │        │ Listening    │          │
      │        │ History      │          │
      │        │ (automatic)  │          │
      │        └──────────────┘          │
      │                                  │
      │  {success, position_ms, ...}     │
      │◀─────────────────────────────────┤
      │                                  │
```

### Dual-Layer Storage

The web player uses a two-tier storage approach for optimal responsiveness:

1. **localStorage (fast cache)**
   - Immediate read/write for responsive playback
   - Used for "resume from last position" on page load
   - Per-browser, cleared when cache is cleared

2. **API/Database (persistent)**
   - Saved every 15 seconds during playback
   - Survives browser clears, available from any device
   - Per-user when auth is enabled (encrypted in auth database)
   - Global when auth is disabled (library database)

---

## Multi-User Support

When `AUTH_ENABLED=true`, each authenticated user has completely independent playback positions stored in the encrypted auth database (SQLCipher).

### How Per-User Tracking Works

- Positions are stored in the `user_positions` table, keyed by `user_id + audiobook_id`
- Two users can read the same audiobook and maintain entirely separate progress
- Positions are encrypted at rest (SQLCipher AES-256)
- Sessions use HTTP-only, Secure, SameSite=Lax cookies for user identification

### Example

```text
User A: "The Stand" → Position: 3h 45m (chapter 12)
User B: "The Stand" → Position: 8h 20m (chapter 31)
```

Each user sees only their own position when they load the web player.

---

## Auth Disabled Fallback

When `AUTH_ENABLED=false` (standalone mode), position tracking operates in single-user global mode:

- Positions are stored in the `audiobooks` table (`playback_position_ms` column)
- No user isolation -- one position per book shared by all browsers
- Position history recorded in the `playback_history` table
- No listening session tracking (listening history requires auth)

This mode is suitable for single-user or LAN-only home server deployments.

---

## Listening History

When auth is enabled, every position save automatically creates or updates a listening session record. This provides a full history of when and how long each user listened to each audiobook.

### Session Model

- A **listening session** starts when a user begins playing a book (first position save)
- Subsequent saves update the session's end position and duration
- Sessions track: start time, end time, start position, end position, and total duration listened

### Viewing History

- **Users**: `GET /api/user/history` -- paginated personal listening history
- **Admins**: `GET /api/admin/activity` -- unified activity log across all users (listening + downloads)
- **Admins**: `GET /api/admin/activity/stats` -- aggregate statistics (top listened, top downloaded, active users)

---

## Web Player Integration

### PlaybackManager Class

The web player's `PlaybackManager` class handles position persistence:

```javascript
class PlaybackManager {
    constructor() {
        this.apiSaveDelay = 15000;  // Save to API every 15 seconds
    }

    // Dual-layer: localStorage (fast) + API (persistent)
    async savePositionToAPI(fileId, positionMs) { ... }
    async getPositionFromAPI(fileId) { ... }

    // Compare localStorage and API, return furthest
    async getBestPosition(fileId) { ... }

    // Force immediate save (on close/pause)
    async flushToAPI(fileId, positionSeconds) { ... }
}
```

### Resume Flow

When you click an audiobook to play:

1. Check localStorage for cached position
2. Fetch position from API (`/api/position/<id>`)
3. Compare positions, use furthest ahead
4. Start playback at best position
5. Save position every 15 seconds

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                           RESUME FLOW                                       │
└─────────────────────────────────────────────────────────────────────────────┘

  Click Play
      │
      ▼
  ┌──────────────────┐
  │ Check localStorage│
  │ for cached pos   │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │ Fetch from API   │
  │ /api/position/id │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │ Compare both     │
  │ Use furthest     │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────┐
  │ Start playback   │
  │ at best position │
  └──────────────────┘
```

---

## API Reference

### Position Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/position/status` | GET | Check position tracking mode (per-user or global) |
| `/api/position/<id>` | GET | Get position for audiobook |
| `/api/position/<id>` | PUT | Update position for audiobook |

### User State Endpoints (Auth Required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/user/history` | GET | Paginated personal listening history |
| `/api/user/downloads` | GET | Paginated personal download history |
| `/api/user/downloads/<id>/complete` | POST | Record a completed download |
| `/api/user/library` | GET | Books the user has interacted with |
| `/api/user/new-books` | GET | Books added since user's last visit |
| `/api/user/new-books/dismiss` | POST | Mark current books as seen |

### Admin Activity Endpoints (Admin Required)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/admin/activity` | GET | Unified activity log (listens + downloads) |
| `/api/admin/activity/stats` | GET | Aggregate activity statistics |

### GET /api/position/status

Check position tracking mode:

```json
{
    "per_user": true
}
```

- `per_user: true` -- auth enabled, positions stored per-user in encrypted auth database
- `per_user: false` -- auth disabled, positions stored globally in library database

### GET /api/position/\<id\>

Get position for a specific audiobook:

```json
{
    "id": 123,
    "title": "The Stand",
    "asin": "B00ABC1234",
    "duration_ms": 54000000,
    "duration_human": "15h 0m 0s",
    "local_position_ms": 3600000,
    "local_position_human": "1h 0m 0s",
    "local_position_updated": "2026-01-07T10:30:00",
    "percent_complete": 6.7
}
```

### PUT /api/position/\<id\>

Update position (from player):

**Request:**

```json
{"position_ms": 3600000}
```

**Response:**

```json
{
    "success": true,
    "audiobook_id": 123,
    "position_ms": 3600000,
    "position_human": "1h 0m 0s",
    "updated_at": "2026-01-07T10:35:00"
}
```

---

## Database Schema

Position data is stored in two databases depending on authentication mode.

### Auth Database (SQLCipher) -- Per-User Positions

When `AUTH_ENABLED=true`, each user's position and history are tracked independently:

```sql
-- Per-user playback positions
CREATE TABLE user_positions (
    user_id INTEGER NOT NULL,
    audiobook_id INTEGER NOT NULL,
    position_ms INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, audiobook_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Per-user listening history (automatic session tracking)
CREATE TABLE user_listening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    position_start_ms INTEGER,
    position_end_ms INTEGER,
    duration_listened_ms INTEGER
);

-- Per-user download tracking
CREATE TABLE user_downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    downloaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    file_format TEXT
);

-- Per-user preferences (new book discovery, etc.)
CREATE TABLE user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    new_books_seen_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### Library Database (SQLite) -- Global Fallback

When `AUTH_ENABLED=false`, the library database stores a single global position:

```sql
-- In audiobooks table
playback_position_ms INTEGER DEFAULT 0,
playback_position_updated TIMESTAMP,

-- Global playback history
CREATE TABLE playback_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    position_ms INTEGER NOT NULL,
    source TEXT NOT NULL,  -- 'local'
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Troubleshooting

### Position Not Resuming

**Symptom:** Player starts from the beginning instead of saved position

**Fixes:**

1. Check that the API is running: `curl -s http://localhost:5001/api/position/status`
2. Hard refresh the page (Ctrl+Shift+R)
3. Check browser console for API errors
4. Verify the audiobook exists in the database

### Position Not Updating in Browser

**Symptom:** Browser shows stale position after listening on another device

**Fixes:**

1. Hard refresh the page (Ctrl+Shift+R) -- this triggers a fresh API fetch
2. Clear localStorage for the site
3. Check that API saves are succeeding (browser console network tab)

### Per-User Positions Not Working

**Symptom:** All users see the same position

**Checks:**

1. Verify `AUTH_ENABLED=true` in configuration
2. Check position status: `curl -s http://localhost:5001/api/position/status` should return `{"per_user": true}`
3. Verify auth database is initialized with `user_positions` table
4. Check API logs for auth database errors

### API Returns 401

**Symptom:** Position endpoints return 401 Unauthorized

**Meaning:** Auth is enabled but the request has no valid session

**Fix:** Log in through the web UI or provide a valid session cookie

---

## History

Position tracking has evolved through several versions:

| Version | Capability |
|---------|-----------|
| v1.0--v4.x | Global position tracking in library database |
| v5.0 | Added per-user position tracking in encrypted auth database |
| v5.0--v6.2 | Supported optional Audible position sync (bidirectional) |
| v6.3.0 | Removed Audible position sync; local-only per-user system |

Previously, Audiobook-Manager supported bidirectional position synchronization with Audible's cloud service, allowing seamless switching between the web player and Audible's official apps. This feature was removed in v6.3.0 in favor of a simpler, local-only per-user architecture. The Audible sync endpoints (`/api/position/sync/<id>`, `/api/position/sync-all`, `/api/position/syncable`) have been removed.

---

*Document Version: 7.0.2*
*Last Updated: 2026-03-14*
