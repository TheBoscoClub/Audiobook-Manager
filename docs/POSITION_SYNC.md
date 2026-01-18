# Position Sync Guide

Bidirectional playback position synchronization between your local Audiobook-Manager library and Audible cloud.

## Table of Contents

1. [Overview](#overview)
2. [How It Works](#how-it-works)
3. [Prerequisites](#prerequisites)
4. [Initial Setup](#initial-setup)
5. [First Sync](#first-sync)
6. [Ongoing Synchronization](#ongoing-synchronization)
7. [API Reference](#api-reference)
8. [Web Player Integration](#web-player-integration)
9. [Troubleshooting](#troubleshooting)

---

## Overview

Position sync allows you to seamlessly switch between listening on Audible's official apps and your self-hosted Audiobook-Manager. When you pause a book on your phone, you can resume at the exact same position in your browser, and vice versa.

### Key Features

- **Bidirectional sync**: Positions flow both ways between local and Audible cloud
- **"Furthest ahead wins"**: Automatic conflict resolution - the more advanced position always takes precedence
- **Batch operations**: Sync hundreds of books in a single operation
- **Position history**: Track playback progress over time
- **Automatic player sync**: Web player saves positions to both localStorage and the API

### Sync Strategy: Furthest Ahead Wins

The sync algorithm is intentionally simple and conservative:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        POSITION SYNC ALGORITHM                               │
└─────────────────────────────────────────────────────────────────────────────┘

  Local Position                 Audible Cloud Position
       │                                   │
       └───────────────┬───────────────────┘
                       │
                       ▼
              ┌───────────────────┐
              │  Compare Positions │
              └───────────────────┘
                       │
       ┌───────────────┼───────────────┐
       │               │               │
       ▼               ▼               ▼
   Local > Audible  Local = Audible  Audible > Local
       │               │               │
       ▼               ▼               ▼
  ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ Push to  │   │ Already  │   │ Pull from│
  │ Audible  │   │ Synced   │   │ Audible  │
  └──────────┘   └──────────┘   └──────────┘
```

**Why "furthest ahead wins"?**
- You never lose progress - rewinding is always manual
- Simple mental model - no complex merge logic
- Prevents accidental position resets from stale devices

---

## How It Works

### System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      POSITION SYNC ARCHITECTURE                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────┐         ┌───────────────────┐         ┌──────────────────┐
│   Web Browser    │         │  Audiobook-Manager │         │   Audible Cloud  │
│   (Player)       │         │       API          │         │                  │
├──────────────────┤         ├───────────────────┤         ├──────────────────┤
│                  │  Every  │                   │  Batch  │                  │
│  localStorage ───┼──15s───▶│  SQLite Database ─┼─ Sync ─▶│  Audible API    │
│  (fast cache)    │  save   │  (persistent)     │         │  lastpositions   │
│                  │         │                   │         │                  │
│  PlaybackManager │         │  position_sync.py │         │  ACR credential  │
│  class           │         │  Flask Blueprint  │         │  for writes      │
└──────────────────┘         └───────────────────┘         └──────────────────┘
        │                              │                           │
        │                              │                           │
        └──────────────────────────────┴───────────────────────────┘
                              │
                              ▼
                    "Furthest ahead wins"
```

### Dual-Layer Storage

The web player uses a two-tier storage approach:

1. **localStorage (fast cache)**
   - Immediate read/write for responsive playback
   - Used for "resume from last position" on page load
   - Per-browser, cleared when cache is cleared

2. **API/Database (persistent)**
   - Saved every 15 seconds during playback
   - Synced to database with position history
   - Survives browser clears, available from any device
   - Required for Audible cloud sync

### ASIN Requirement

Position sync with Audible requires the book's **ASIN** (Amazon Standard Identification Number). Books without ASINs can still have local position tracking, but cannot sync with Audible cloud.

To populate ASINs, run:
```bash
cd rnd
python3 populate_asins.py --dry-run   # Preview matches
python3 populate_asins.py             # Update database
```

---

## Prerequisites

### 1. audible-cli Setup

You need [audible-cli](https://github.com/mkb79/audible-cli) installed and authenticated:

```bash
# Install audible-cli
pip install audible-cli

# Authenticate (interactive)
audible quickstart

# Verify authentication works
audible library list --limit 5
```

This creates `~/.audible/audible.json` with your Audible credentials.

### 2. Python Dependencies

The position sync module requires the `audible` Python library:

```bash
# In your virtual environment
pip install audible

# Or add to requirements.txt
echo "audible>=0.9.0" >> requirements.txt
```

### 3. Encrypted Credential Storage

For security, the Audible auth file password is stored encrypted using Fernet symmetric encryption (PBKDF2-derived key). The `credential_manager.py` module handles this.

---

## Initial Setup

### Step 1: Authenticate with Audible CLI

If you haven't already:

```bash
audible quickstart
```

Follow the interactive prompts to log in with your Amazon account.

### Step 2: Store Credential for Position Sync

Run the position sync test tool to store your credential:

```bash
cd /opt/audiobooks/rnd
python3 position_sync_test.py list
```

On first run, you'll be prompted for your audible.json password. This is the password you set during `audible quickstart`. The password is encrypted and stored at `~/.audible/position_sync_credentials.enc`.

**Expected output:**
```
🔐 Enter your audible.json password: ********
💾 Credential stored successfully
📖 Fetching audiobooks with ASINs...
Found 724 audiobooks with ASINs
```

### Step 3: Populate ASINs

If your local audiobooks don't have ASINs in the database:

```bash
cd /opt/audiobooks/rnd

# Preview what would be matched
python3 populate_asins.py --dry-run

# Apply changes
python3 populate_asins.py
```

This matches local books to your Audible library by title (fuzzy matching, 70% threshold) and populates the ASIN field.

### Step 4: Verify Setup

Check that position sync is ready:

```bash
curl -s http://localhost:5001/api/position/status | python3 -m json.tool
```

**Expected output:**
```json
{
    "audible_available": true,
    "credential_stored": true,
    "auth_file_exists": true
}
```

---

## First Sync

### Batch Sync All Books

To sync all your audiobooks with Audible in one operation:

#### Using the CLI Tool

```bash
cd /opt/audiobooks/rnd
python3 position_sync_test.py batch-sync
```

**Output:**
```
📊 Batch Sync Results:
   Total synced: 724
   Pulled from Audible: 401
   Pushed to Audible: 2
   Already synced: 321
   Errors: 0
```

#### Using the API

```bash
curl -X POST http://localhost:5001/api/position/sync-all | python3 -m json.tool
```

### Sync a Single Book

For individual book sync:

```bash
# By audiobook ID
curl -X POST http://localhost:5001/api/position/sync/123 | python3 -m json.tool
```

**Response:**
```json
{
    "audiobook_id": 123,
    "title": "The Stand",
    "asin": "B00ABC1234",
    "local_position_ms": 3600000,
    "local_position_human": "1h 0m 0s",
    "audible_position_ms": 7200000,
    "audible_position_human": "2h 0m 0s",
    "action": "pulled_from_audible",
    "final_position_ms": 7200000,
    "final_position_human": "2h 0m 0s"
}
```

---

## Ongoing Synchronization

### Web Player Auto-Sync

The web player automatically saves positions:

1. **Every 15 seconds** during active playback
2. **On pause** - immediate save
3. **On close** - flush to API before closing

These saves go to both localStorage (immediate) and the API (for sync).

### Periodic Batch Sync

For comprehensive sync across all devices, run batch-sync periodically:

```bash
# Add to crontab for hourly sync
0 * * * * /opt/audiobooks/library/venv/bin/python3 /opt/audiobooks/rnd/position_sync_test.py batch-sync >> /var/log/audiobooks/position-sync.log 2>&1
```

Or create a systemd timer:

```ini
# /etc/systemd/system/audiobooks-position-sync.timer
[Unit]
Description=Sync audiobook positions with Audible

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/audiobooks-position-sync.service
[Unit]
Description=Audiobook position sync

[Service]
Type=oneshot
User=audiobooks
ExecStart=/opt/audiobooks/library/venv/bin/python3 /opt/audiobooks/rnd/position_sync_test.py batch-sync
WorkingDirectory=/opt/audiobooks/rnd
```

### Manual Sync from Web UI

The web player's resume functionality automatically fetches the best position (comparing localStorage and API). To trigger a full Audible sync for a specific book, use the API:

```bash
curl -X POST http://localhost:5001/api/position/sync/123
```

---

## API Reference

### Position Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/position/status` | GET | Check if position sync is available |
| `/api/position/<id>` | GET | Get position for audiobook |
| `/api/position/<id>` | PUT | Update local position |
| `/api/position/sync/<id>` | POST | Sync single book with Audible |
| `/api/position/sync-all` | POST | Batch sync all books |
| `/api/position/syncable` | GET | List all syncable books |
| `/api/position/history/<id>` | GET | Get position history |

### GET /api/position/status

Check sync availability:

```json
{
    "audible_available": true,
    "credential_stored": true,
    "auth_file_exists": true
}
```

### GET /api/position/<id>

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
    "audible_position_ms": 3600000,
    "audible_position_human": "1h 0m 0s",
    "audible_position_updated": "2026-01-07T09:15:00",
    "position_synced_at": "2026-01-07T10:30:00",
    "percent_complete": 6.7,
    "syncable": true
}
```

### PUT /api/position/<id>

Update local position (from player):

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

### POST /api/position/sync/<id>

Sync single book with Audible:

```json
{
    "audiobook_id": 123,
    "title": "The Stand",
    "asin": "B00ABC1234",
    "local_position_ms": 3600000,
    "local_position_human": "1h 0m 0s",
    "audible_position_ms": 7200000,
    "audible_position_human": "2h 0m 0s",
    "action": "pulled_from_audible",
    "final_position_ms": 7200000,
    "final_position_human": "2h 0m 0s"
}
```

Actions:
- `pulled_from_audible`: Audible was ahead, updated local
- `pushed_to_audible`: Local was ahead, updated Audible
- `already_synced`: Positions matched

### POST /api/position/sync-all

Batch sync all books with ASINs:

```json
{
    "total": 724,
    "pulled_from_audible": 401,
    "pushed_to_audible": 2,
    "already_synced": 321,
    "failed": 0,
    "results": [...]
}
```

### GET /api/position/syncable

List all books that can sync with Audible (have ASINs):

```json
{
    "total": 724,
    "books": [
        {
            "id": 123,
            "title": "The Stand",
            "author": "Stephen King",
            "asin": "B00ABC1234",
            "duration_human": "15h 0m 0s",
            "local_position_human": "1h 0m 0s",
            "audible_position_human": "1h 0m 0s",
            "percent_complete": 6.7,
            "last_synced": "2026-01-07T10:30:00"
        }
    ]
}
```

### GET /api/position/history/<id>

Get position history for an audiobook:

```json
{
    "audiobook_id": 123,
    "history": [
        {
            "position_ms": 3600000,
            "position_human": "1h 0m 0s",
            "source": "sync",
            "recorded_at": "2026-01-07T10:30:00"
        },
        {
            "position_ms": 3500000,
            "position_human": "58m 20s",
            "source": "local",
            "recorded_at": "2026-01-07T10:15:00"
        }
    ]
}
```

Sources:
- `local`: Saved from web player
- `audible`: Pulled from Audible cloud
- `sync`: Result of sync operation

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

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           RESUME FLOW                                        │
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

## Troubleshooting

### Position Sync Status Shows False

**Symptom:** `/api/position/status` returns `audible_available: false`

**Causes and fixes:**
1. **Missing audible library**: `pip install audible`
2. **Missing credential_manager**: Ensure `rnd/` is in Python path
3. **Import error**: Check API logs for specific error

### No Stored Credential

**Symptom:** `credential_stored: false` in status

**Fix:**
```bash
cd /opt/audiobooks/rnd
python3 position_sync_test.py list
# Enter your audible.json password when prompted
```

### Auth File Not Found

**Symptom:** `auth_file_exists: false`

**Fix:**
```bash
# Run audible quickstart to create auth file
audible quickstart
```

### Books Not Syncing

**Symptom:** Sync returns 0 books

**Causes:**
1. **No ASINs**: Run `populate_asins.py` to match books
2. **Source filter**: Only Audible-sourced books have ASINs by default

**Check syncable count:**
```bash
curl -s http://localhost:5001/api/position/syncable | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])"
```

### Push to Audible Fails

**Symptom:** `action: push_failed` in sync results

**Causes:**
1. **ACR credential expired**: Re-run `position_sync_test.py list` to refresh
2. **Rate limiting**: Wait a few minutes and retry
3. **Invalid ASIN**: Book may not be in your Audible library

### Position Not Updating in Browser

**Symptom:** Browser shows old position after sync

**Fixes:**
1. Hard refresh the page (Ctrl+Shift+R)
2. Clear localStorage for the site
3. Check browser console for API errors

### API Returns 503

**Symptom:** `/api/position/sync/*` returns 503 error

**Meaning:** Audible library not available (not installed or import failed)

**Fix:**
```bash
# Check the import error
curl -s http://localhost:5001/api/position/status | python3 -m json.tool
# Look at "error" field for specific import failure
```

---

## Database Schema

Position sync uses these database columns:

```sql
-- In audiobooks table
playback_position_ms INTEGER DEFAULT 0,      -- Local position in milliseconds
playback_position_updated TIMESTAMP,         -- When local position was updated
audible_position_ms INTEGER,                 -- Last known Audible position
audible_position_updated TIMESTAMP,          -- When Audible position was fetched
position_synced_at TIMESTAMP,                -- Last successful sync timestamp

-- Playback history table
CREATE TABLE playback_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    position_ms INTEGER NOT NULL,
    source TEXT NOT NULL,  -- 'local', 'audible', 'sync'
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

To view syncable books directly:
```sql
SELECT id, title, asin,
       playback_position_ms, audible_position_ms, position_synced_at
FROM audiobooks
WHERE asin IS NOT NULL AND asin != ''
ORDER BY position_synced_at DESC
LIMIT 20;
```

---

## Security Considerations

### Credential Storage

- Audible auth password is encrypted using **Fernet** (AES-128-CBC)
- Key derived using **PBKDF2** with 480,000 iterations
- Stored at `~/.audible/position_sync_credentials.enc`
- Machine-bound (derived from hostname + username)

### API Security

- Position API runs on localhost only (127.0.0.1:5001)
- Accessible via HTTPS proxy on 8443
- No authentication required (assumes single-user deployment)
- For multi-user deployments, add authentication layer

### Audible API Access

- Uses official Audible API endpoints
- Respects rate limits (batch operations limited to 25 ASINs per request)
- ACR credentials are short-lived and book-specific
- No Audible account credentials stored (only auth file password)

---

*Document Version: 4.0.0*
*Last Updated: 2026-01-17*
