# Audiobook-Manager Architecture Guide

This document describes the system architecture, installation workflows, storage layout, and recommendations for optimal deployment of Audiobook-Manager.

## Table of Contents

1. [System Overview](#system-overview)
2. [Component Architecture](#component-architecture)
3. [Scanner Module Architecture](#scanner-module-architecture)
4. [API Module Architecture](#api-module-architecture)
5. [Position Sync Architecture](#position-sync-architecture)
6. [Periodicals Architecture](#periodicals-architecture)
7. [Systemd Services Reference](#systemd-services-reference)
8. [Scripts Reference](#scripts-reference)
9. [Installation Workflow](#installation-workflow)
10. [Upgrade Workflow](#upgrade-workflow)
11. [Migration Workflow](#migration-workflow)
12. [Storage Layout](#storage-layout)
13. [Storage Recommendations](#storage-recommendations)
14. [Filesystem Recommendations](#filesystem-recommendations)
15. [Kernel Compatibility](#kernel-compatibility)
16. [Quick Reference](#quick-reference)
17. [Appendix: Storage Decision Tree](#appendix-storage-decision-tree)

---

## System Overview

Audiobook-Manager consists of six logical component groups:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AUDIOBOOK-MANAGER SYSTEM                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │   APPLICATION   │  │    USER DATA    │  │    DATABASE     │             │
│  │                 │  │                 │  │                 │             │
│  │ • Python code   │  │ • Library/      │  │ • SQLite DB     │             │
│  │ • Web UI        │  │ • Sources/      │  │ • Indexes       │             │
│  │ • Scripts       │  │ • Supplements/  │  │ • Metadata      │             │
│  │ • Converter     │  │ • .covers/      │  │ • Positions     │             │
│  │                 │  │ • logs/         │  │ • Periodicals   │             │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘             │
│           │                    │                    │                       │
│           ▼                    ▼                    ▼                       │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │                      CONFIGURATION                          │           │
│  │  /etc/audiobooks/audiobooks.conf  |  Environment Variables  │           │
│  └─────────────────────────────────────────────────────────────┘           │
│           │                                                                 │
│           ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │                    EXTERNAL INTEGRATIONS                     │           │
│  │  Position Sync: Audible Cloud  |  Periodicals: Audible API   │           │
│  │                Metadata: Audible Library                     │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Design Principles

1. **Separation of Concerns**: Application code, user data, and database are independent
2. **Symlink Architecture**: Scripts installed once, accessed via symlinks
3. **Configuration Hierarchy**: Defaults → System config → User config → Environment
4. **Storage Optimization**: Each component placed on appropriate storage tier

---

## Component Architecture

### Component Responsibilities

| Component | Purpose | I/O Pattern | Latency Sensitivity |
|-----------|---------|-------------|---------------------|
| **Application** | Python code, web assets, scripts | Read-heavy on startup | Low |
| **Library** | Converted audiobooks (Opus) | Sequential streaming | Medium |
| **Sources** | Original AAXC files | Sequential read during conversion | Low |
| **Database** | SQLite with metadata, indexes | Random read/write | **High** |
| **Covers** | Cover art images (JPEG/PNG) | Random read | Medium |
| **Logs** | Application and conversion logs | Append-only write | Low |

### Symlink Architecture

```
/usr/local/bin/                          /opt/audiobooks/scripts/
┌──────────────────────┐                 ┌──────────────────────────────────┐
│ audiobooks-convert ──┼────symlink────▶ │ audiobook-convert │
│ audiobooks-download ─┼────symlink────▶ │ download-new-audiobooks          │
│ audiobooks-move ─────┼────symlink────▶ │ move-staged-audiobooks           │
│ audiobook-upgrade ──┼────symlink────▶ │ upgrade.sh                       │
│ audiobooks-migrate ──┼────symlink────▶ │ migrate-api.sh                   │
└──────────────────────┘                 └──────────────────────────────────┘
         │                                              │
         │                                              │
    Commands in PATH                           Canonical location
    (auto-updated via symlinks)                (updated by upgrade.sh)
```

**Benefits:**
- `upgrade.sh` updates `/opt/audiobooks/scripts/` → all commands updated automatically
- No need to re-create symlinks after upgrades
- Single source of truth for each script

### Security Architecture

The API service runs with systemd security hardening (`NoNewPrivileges=yes`, `ProtectSystem=strict`) which prevents direct `sudo` usage. Privileged operations use a **privilege-separated helper service pattern**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PRIVILEGE-SEPARATED HELPER PATTERN                        │
└─────────────────────────────────────────────────────────────────────────────┘

  Web UI                    API Service                  Helper Service
  (Browser)                 (audiobooks user)            (root)
     │                           │                           │
     │  POST /api/system/        │                           │
     │  services/mover/stop      │                           │
     ├──────────────────────────▶│                           │
     │                           │                           │
     │                           │  Write request JSON to    │
     │                           │  /var/lib/audiobooks/     │
     │                           │  .control/upgrade-request │
     │                           ├──────────────────────────▶│
     │                           │                           │
     │                           │      Path unit detects    │
     │                           │      file, triggers       │
     │                           │      helper service       │
     │                           │                           │
     │                           │                           │  systemctl stop
     │                           │                           │  audiobook-mover
     │                           │                           │
     │                           │  Write status JSON to     │
     │                           │  .control/upgrade-status  │
     │                           │◀────────────────────────────
     │                           │                           │
     │                           │  Poll status file         │
     │                           │  until complete           │
     │                           │                           │
     │  {"success": true,        │                           │
     │   "message": "Stopped"}   │                           │
     │◀──────────────────────────│                           │
     │                           │                           │
```

**Components:**

| Unit | Purpose |
|------|---------|
| `audiobook-upgrade-helper.path` | Watches `/var/lib/audiobooks/.control/upgrade-request` |
| `audiobook-upgrade-helper.service` | Runs as root, processes privileged operations |
| `/var/lib/audiobooks/.control/` | IPC directory (owned by audiobooks user) |

**Supported Operations:**
- Service control: start, stop, restart individual services
- Bulk operations: start-all, stop-all
- Application upgrades: from GitHub or local project directory

**Why /var/lib/audiobooks/.control/ instead of /run/audiobooks/:**

The API runs with `ProtectSystem=strict` which creates a read-only filesystem overlay. While `RuntimeDirectory=` can create `/run/audiobooks`, the sandboxed namespace sees it with root ownership (not audiobooks), preventing writes. Using `/var/lib/audiobooks/.control/` works because it's explicitly listed in `ReadWritePaths`.

### Reverse Proxy Architecture

The HTTPS reverse proxy (`proxy_server.py`) terminates SSL and forwards requests to the Flask API. A critical requirement is filtering **hop-by-hop headers** per RFC 2616 and PEP 3333:

```python
# Headers that MUST NOT be forwarded by proxies
HOP_BY_HOP_HEADERS = frozenset({
    'connection', 'keep-alive', 'proxy-authenticate',
    'proxy-authorization', 'te', 'trailers',
    'transfer-encoding', 'upgrade',
})

# Filter when forwarding response headers
for header, value in response.headers.items():
    if header.lower() not in HOP_BY_HOP_HEADERS:
        self.send_header(header, value)
```

**Symptoms of Missing Hop-by-hop Filter:**
- `AssertionError: Connection is a "hop-by-hop" header` from Waitress/WSGI
- Silently dropped API responses
- Intermittent failures only when accessed through proxy
- Works when hitting API directly on port 5001

**Why This Matters:**
HTTP/1.1 defines hop-by-hop headers as connection-specific; they must be consumed by the first proxy and not forwarded. WSGI servers (Waitress, Gunicorn) reject responses containing these headers, causing the proxy to appear to "hang" or "fail silently."

### Service User Permissions

The `audiobooks` service user must have write access to all data directories:

```
Owner: audiobooks:audiobooks
├── /var/lib/audiobooks/     # Database, indexes, control files
├── /srv/audiobooks/Sources/ # Downloaded AAXC files (MUST be writable)
├── /srv/audiobooks/Library/ # Converted OPUS files
└── /srv/audiobooks/.covers/ # Cover art cache
```

**Common Issue**: Downloads or writes "fail silently" when directories are owned by a different user (e.g., `root` or `bosco`) but the service runs as `audiobooks`. The service can start successfully but cannot perform write operations.

**Detection**:
```bash
# Check for permission mismatches
for dir in /var/lib/audiobooks /srv/audiobooks/Sources /srv/audiobooks/Library; do
    owner=$(stat -c %U "$dir" 2>/dev/null)
    [[ "$owner" != "audiobooks" ]] && echo "WARN: $dir owned by $owner"
done
```

---

## Scanner Module Architecture

The scanner subsystem handles metadata extraction, library scanning, and database imports. Located in `library/scanner/`.

### Scanner Components

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       SCANNER MODULE HIERARCHY                               │
└─────────────────────────────────────────────────────────────────────────────┘

  library/scanner/
  ├── metadata_utils.py      # Core metadata extraction (ffprobe wrapper)
  ├── scan_audiobooks.py     # Full library scanner
  ├── add_new_audiobooks.py  # Incremental scanner for new files
  ├── import_single.py       # Single-directory inline importer
  ├── find_missing_audiobooks.py  # Detect missing/moved files
  └── create_priority_list.py     # Conversion queue prioritization
```

### Data Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AUDIOBOOK DATA PIPELINE                              │
└─────────────────────────────────────────────────────────────────────────────┘

  STAGE 1: DOWNLOAD                      STAGE 2: CONVERT
  ┌─────────────────────┐                ┌─────────────────────┐
  │ download-new-       │                │ convert-audiobooks- │
  │ audiobooks          │                │ opus-parallel       │
  │                     │                │                     │
  │ Audible CLI         │                │ FFmpeg + AAX keys   │
  │     ↓               │                │     ↓               │
  │ Sources/*.aaxc      ├───────────────▶│ Staging/*.opus      │
  └─────────────────────┘                └─────────────────────┘
                                                   │
                                                   ▼
  STAGE 4: IMPORT                        STAGE 3: MOVE
  ┌─────────────────────┐                ┌─────────────────────┐
  │ import_single.py    │                │ move-staged-        │
  │                     │                │ audiobooks          │
  │ Extract metadata    │                │                     │
  │ Insert to SQLite    │◀───────────────│ rsync to Library/   │
  │ Extract cover art   │                │ Generate checksums  │
  └─────────────────────┘                └─────────────────────┘
          │
          ▼
  ┌─────────────────────┐
  │ SQLite Database     │
  │ • audiobooks table  │
  │ • FTS5 index        │
  │ • Lookup tables     │
  └─────────────────────┘
```

### Metadata Extraction

The `metadata_utils.py` module wraps ffprobe for metadata extraction:

```python
# CRITICAL: Opus files store metadata differently
def get_file_metadata(filepath):
    data = run_ffprobe(filepath)

    # Check format-level tags first (MP3, M4A, M4B)
    tags = data.get("format", {}).get("tags", {})

    # Fall back to stream-level tags (Opus/Ogg uses Vorbis comments)
    if not tags:
        streams = data.get("streams", [])
        if streams:
            tags = streams[0].get("tags", {})

    return tags
```

**Why this matters**: Opus files store metadata in Vorbis comments on the audio stream (`streams[0].tags`), not in the container format. Code that only checks `format.tags` will get empty metadata for Opus files.

### Import Single Module

The `import_single.py` module provides inline database import called directly by `move-staged-audiobooks`:

```bash
# In move-staged-audiobooks:
if python3 "${AUDIOBOOKS_HOME}/library/scanner/import_single.py" "$DEST"; then
    log "✓ Imported: $DIR_NAME"
fi
```

This eliminates the delay between file arrival and database visibility, making newly converted audiobooks immediately browsable in the web UI.

---

## API Module Architecture

The Flask API uses a modular blueprint architecture (`library/backend/api_modular/`).

### Blueprint Organization

| Blueprint | Prefix | Purpose |
|-----------|--------|---------|
| `audiobooks_bp` | `/api` | Main listing, streaming, single book |
| `collections_bp` | `/api` | Predefined genre-based collections |
| `editions_bp` | `/api` | Edition detection and grouping |
| `duplicates_bp` | `/api` | Duplicate detection (hash/title) |
| `supplements_bp` | `/api` | PDF, ebook companion files |
| `utilities_bp` | `/api` | CRUD, imports, exports, maintenance |
| `position_bp` | `/api` | Playback position sync |
| `periodicals_bp` | `/api/v1` | Periodicals/Reading Room |

### Utilities Operations Submodules

The `utilities_ops/` package contains specialized operation handlers (refactored from monolithic `utilities_ops.py` in v3.9.8):

```
library/backend/api_modular/utilities_ops/
├── __init__.py      # Re-exports all operations
├── audible.py       # Audible API operations (download, metadata sync)
├── hashing.py       # Hash generation for duplicate detection
├── library.py       # Library content management (rescan, cleanup)
├── maintenance.py   # Database maintenance (vacuum, reindex, FTS rebuild)
└── status.py        # Operation status endpoint handlers
```

| Module | Key Functions | API Endpoints |
|--------|--------------|---------------|
| `audible.py` | `download_audiobook()`, `sync_metadata()` | POST `/api/utilities/download` |
| `hashing.py` | `generate_hashes()`, `update_checksums()` | POST `/api/utilities/generate-hashes` |
| `library.py` | `rescan_library()`, `cleanup_missing()` | POST `/api/utilities/rescan` |
| `maintenance.py` | `vacuum_database()`, `rebuild_fts()` | POST `/api/utilities/maintenance` |
| `status.py` | `get_operation_status()` | GET `/api/utilities/status/<id>` |

---

## Position Sync Architecture

Audiobook-Manager supports bidirectional playback position synchronization with Audible cloud, allowing seamless switching between the web interface and Audible's official apps.

### Position Sync Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      POSITION SYNC ARCHITECTURE                              │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────┐         ┌───────────────────┐         ┌──────────────────┐
  │   Web Browser    │         │  Audiobook-Manager│         │   Audible Cloud  │
  │   (Player)       │         │       API         │         │                  │
  ├──────────────────┤         ├───────────────────┤         ├──────────────────┤
  │                  │  Every  │                   │  Batch  │                  │
  │  localStorage ───┼──15s───▶│  SQLite Database ─┼─ Sync ─▶│  Audible API     │
  │  (fast cache)    │  save   │  (persistent)     │         │  lastpositions   │
  │                  │         │                   │         │                  │
  │  PlaybackManager │         │  position_sync.py │         │  ACR credential  │
  │  class           │         │  Flask Blueprint  │         │  for writes      │
  └──────────────────┘         └───────────────────┘         └──────────────────┘
```

### Sync Strategy: Furthest Ahead Wins

The sync algorithm uses a simple, conservative approach:

| Condition | Action | Result |
|-----------|--------|--------|
| Audible > Local | Pull from Audible | Local updated to Audible position |
| Local > Audible | Push to Audible | Audible updated to local position |
| Local = Audible | No action | Already synchronized |

**Rationale:** You never lose progress. Rewinding is always a manual action.

### Component Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    POSITION SYNC DATA FLOW                                   │
└─────────────────────────────────────────────────────────────────────────────┘

  Web Player                   Flask API                    Audible Cloud
      │                            │                             │
      │  Every 15s during play     │                             │
      ├────────────────────────────▶                             │
      │  PUT /api/position/<id>    │                             │
      │  {position_ms: 3600000}    │                             │
      │                            │                             │
      │                            │  Store in SQLite            │
      │                            │  + playback_history table   │
      │                            │                             │
      │  On sync request           │                             │
      │  POST /api/position/sync   │                             │
      ├────────────────────────────▶                             │
      │                            │                             │
      │                            │  Fetch Audible position     │
      │                            ├────────────────────────────▶│
      │                            │  GET /1.0/annotations/      │
      │                            │      lastpositions          │
      │                            │◀────────────────────────────┤
      │                            │                             │
      │                            │  Compare positions          │
      │                            │  (furthest ahead wins)      │
      │                            │                             │
      │                            │  If local > audible:        │
      │                            │    Get ACR credential       │
      │                            ├────────────────────────────▶│
      │                            │  POST /1.0/content/{asin}/  │
      │                            │       licenserequest        │
      │                            │◀────────────────────────────┤
      │                            │                             │
      │                            │    Push position            │
      │                            ├────────────────────────────▶│
      │                            │  PUT /1.0/lastpositions/    │
      │                            │      {asin}                 │
      │                            │                             │
      │  Sync result               │                             │
      │◀────────────────────────────                             │
      │  {action: "pushed/pulled"} │                             │
```

### Dual-Layer Storage

The web player uses two storage tiers for optimal responsiveness:

| Layer | Purpose | Persistence | Sync Capability |
|-------|---------|-------------|-----------------|
| **localStorage** | Fast cache for immediate resume | Per-browser, volatile | None |
| **API/Database** | Persistent storage | Survives browser clears | Yes (Audible cloud) |

### ASIN Requirement

Position sync with Audible requires the book's **ASIN** (Amazon Standard Identification Number):

- Books with ASINs can sync bidirectionally with Audible
- Books without ASINs have local-only position tracking
- Use `rnd/populate_asins.py` to match local books to Audible library

### Database Schema (Position Fields)

```sql
-- In audiobooks table
playback_position_ms INTEGER DEFAULT 0,      -- Current local position
playback_position_updated TIMESTAMP,         -- When local was last updated
audible_position_ms INTEGER,                 -- Last known Audible position
audible_position_updated TIMESTAMP,          -- When Audible was fetched
position_synced_at TIMESTAMP,                -- Last successful sync

-- Position history tracking
CREATE TABLE playback_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    position_ms INTEGER NOT NULL,
    source TEXT NOT NULL,  -- 'local', 'audible', 'sync'
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Content Type Classification

The `content_type` field in the audiobooks table stores Audible's content classification:

| Content Type | Description | Main Library |
|-------------|-------------|--------------|
| `Product` | Standard audiobooks | Yes |
| `Lecture` | Educational content (Great Courses, etc.) | Yes |
| `Performance` | Dramatized productions | Yes |
| `Speech` | Speeches, talks | Yes |
| `Podcast` | Podcast episodes | No (Reading Room) |
| `Newspaper/Magazine` | News content | No (Reading Room) |
| `Radio/TV Program` | Broadcast content | No (Reading Room) |
| `Show` | Series-based content | No (Reading Room) |

The `library_audiobooks` view filters the main library to exclude periodical content types:

```sql
CREATE VIEW library_audiobooks AS
SELECT * FROM audiobooks
WHERE content_type IN ('Product', 'Lecture', 'Performance', 'Speech')
   OR content_type IS NULL;
```

This separation ensures the main library displays full-length audiobooks while periodical content is managed through the Reading Room.

### Security: Credential Storage

Audible authentication credentials are stored securely:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CREDENTIAL STORAGE                                        │
└─────────────────────────────────────────────────────────────────────────────┘

  ~/.audible/
  ├── audible.json                    # Audible auth (from audible-cli)
  └── position_sync_credentials.enc   # Encrypted password for audible.json

  Encryption:
  • Algorithm: Fernet (AES-128-CBC)
  • Key derivation: PBKDF2 with 480,000 iterations
  • Salt: Machine-bound (hostname + username hash)
```

For complete setup instructions, see [Position Sync Guide](POSITION_SYNC.md).

---

## Periodicals Architecture

The Periodicals subsystem handles episodic Audible content (podcasts, newspapers, meditation series) separately from the main audiobook library. This separation provides cleaner organization and avoids cluttering the library with hundreds of short episodes.

### Periodicals Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      PERIODICALS "READING ROOM"                              │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────┐         ┌───────────────────┐         ┌──────────────────┐
  │   Web Browser    │         │  Audiobook-Manager│         │   Audible API    │
  │ (Reading Room)   │         │       API         │         │                  │
  ├──────────────────┤         ├───────────────────┤         ├──────────────────┤
  │                  │  REST   │                   │  Sync   │                  │
  │  periodicals.html├────────▶│  periodicals.py  ─┼────────▶│  /library        │
  │  (category tabs) │  API    │  Flask Blueprint  │  Timer  │  /episodes       │
  │                  │         │                   │         │                  │
  │  SSE Status ◀────┼─────────┤  sync_periodicals │         │                  │
  │  (real-time)     │ stream  │  background job   │         │                  │
  └──────────────────┘         └───────────────────┘         └──────────────────┘
```

### Content Categories

| Category | Content Type | Examples |
|----------|-------------|----------|
| **podcast** | Audio shows, interviews | Michelle Obama: The Light Podcast |
| **news** | Newspapers, magazines | NY Times Daily, Washington Post |
| **meditation** | Guided meditation, wellness | Aaptiv Meditation, MoveWith |
| **other** | Trailers, promos, misc | Book trailers, previews |

### Data Flow: Parent/Child ASIN Structure

Audible organizes episodic content using a parent/child relationship:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PARENT/CHILD ASIN STRUCTURE                               │
└─────────────────────────────────────────────────────────────────────────────┘

  Parent ASIN (Series)                    Child ASINs (Episodes)
  ┌─────────────────────┐                 ┌─────────────────────┐
  │ B07G8DJNFV          │                 │ B07H1234AB (Ep 1)   │
  │ "Aaptiv Meditation" │ ───contains───▶ │ B07H1234CD (Ep 2)   │
  │ category: meditation│                 │ B07H1234EF (Ep 3)   │
  │ episode_count: 47   │                 │ ... (47 total)      │
  └─────────────────────┘                 └─────────────────────┘

  The sync process:
  1. User adds parent ASIN to skip list (main library ignores it)
  2. Periodicals sync fetches episode list from Audible
  3. Episodes stored in periodicals table with parent reference
  4. User selects episodes for download via Reading Room UI
```

### Sync Mechanism

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PERIODICALS SYNC FLOW                                     │
└─────────────────────────────────────────────────────────────────────────────┘

  systemd timer                      sync-periodicals-index              Audible
  (06:00, 18:00)                          script                          API
       │                                    │                              │
       │  Trigger service                   │                              │
       ├───────────────────────────────────▶│                              │
       │                                    │                              │
       │                                    │  Read skip list              │
       │                                    │  (parent ASINs)              │
       │                                    │                              │
       │                                    │  For each parent:            │
       │                                    ├─────────────────────────────▶│
       │                                    │  GET /library/{parent_asin}  │
       │                                    │◀─────────────────────────────┤
       │                                    │  {episodes: [...]}           │
       │                                    │                              │
       │                                    │  Rate limit: 1 req / 2 sec   │
       │                                    │                              │
       │                                    │  Update SQLite               │
       │                                    │  • Insert new episodes       │
       │                                    │  • Update metadata           │
       │                                    │  • Emit SSE progress         │
       │                                    │                              │
       │                                    │  Complete                    │
       │◀───────────────────────────────────┤                              │

  Manual trigger via UI:
  POST /api/v1/periodicals/sync/trigger  →  Same flow as above
```

### Database Schema

```sql
-- Periodicals content index
CREATE TABLE IF NOT EXISTS periodicals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_asin TEXT NOT NULL,           -- Series/show ASIN
    child_asin TEXT,                     -- Episode ASIN (NULL for parent entry)
    title TEXT NOT NULL,                 -- Series title (parent) or episode title
    episode_title TEXT,                  -- Episode-specific title
    episode_number INTEGER,              -- Episode sequence number
    category TEXT NOT NULL DEFAULT 'podcast',  -- podcast, news, meditation, other
    duration_ms INTEGER,                 -- Episode duration
    release_date TEXT,                   -- Episode release date
    is_downloaded INTEGER DEFAULT 0,     -- 1 if episode is downloaded
    download_requested INTEGER DEFAULT 0, -- 1 if queued for download
    last_synced TEXT,                    -- Last sync timestamp
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(parent_asin, child_asin)
);

-- Sync status tracking
CREATE TABLE IF NOT EXISTS periodicals_sync_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT DEFAULT 'running',       -- running, completed, failed
    parents_processed INTEGER DEFAULT 0,
    episodes_found INTEGER DEFAULT 0,
    error_message TEXT
);

-- Views for common queries
CREATE VIEW periodicals_download_queue AS
SELECT * FROM periodicals
WHERE download_requested = 1 AND is_downloaded = 0;

CREATE VIEW periodicals_summary AS
SELECT parent_asin, title, category,
       COUNT(child_asin) as episode_count,
       SUM(is_downloaded) as downloaded_count,
       SUM(download_requested) as queued_count,
       MAX(last_synced) as last_synced
FROM periodicals
WHERE child_asin IS NOT NULL
GROUP BY parent_asin;
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/periodicals` | List all periodical parents with counts |
| `GET` | `/api/v1/periodicals/<asin>` | List episodes for a parent |
| `GET` | `/api/v1/periodicals/<asin>/<episode>` | Get episode details |
| `POST` | `/api/v1/periodicals/download` | Queue episodes for download |
| `DELETE` | `/api/v1/periodicals/download/<asin>` | Cancel queued download |
| `GET` | `/api/v1/periodicals/sync/status` | **SSE stream** for real-time sync status |
| `POST` | `/api/v1/periodicals/sync/trigger` | Manually trigger sync |
| `GET` | `/api/v1/periodicals/categories` | List categories with counts |

### Real-Time Updates via SSE

The Reading Room UI receives real-time sync status via Server-Sent Events:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SERVER-SENT EVENTS (SSE)                                  │
└─────────────────────────────────────────────────────────────────────────────┘

  Browser                                   Flask API
     │                                          │
     │  GET /api/v1/periodicals/sync/status     │
     │  Accept: text/event-stream               │
     ├─────────────────────────────────────────▶│
     │                                          │
     │  HTTP 200 OK                             │
     │  Content-Type: text/event-stream         │
     │◀─────────────────────────────────────────┤
     │                                          │
     │  data: {"status": "running",             │
     │         "parent": "B07G8DJNFV",          │
     │         "progress": "3/10"}              │
     │◀─────────────────────────────────────────┤
     │                                          │
     │  data: {"status": "completed",           │
     │         "episodes_found": 127}           │
     │◀─────────────────────────────────────────┤
     │                                          │

  Advantages over WebSockets:
  • Simpler: unidirectional (server → client)
  • HTTP-native: works through proxies
  • Auto-reconnect: browser handles reconnection
  • No additional dependencies
```

### Security Considerations

1. **ASIN Validation**: All ASIN inputs validated against regex `^[A-Z0-9]{10}$`
2. **XSS Prevention**: UI uses `createElement`/`textContent` (no `innerHTML`)
3. **Rate Limiting**: Audible API calls rate-limited to 1 request per 2 seconds
4. **Skip List**: Parent ASINs stored in `/var/lib/audiobooks/audiobook-skip-list.txt`

### Systemd Units

| Unit | Type | Purpose |
|------|------|---------|
| `audiobook-periodicals-sync.timer` | Timer | Triggers sync at 06:00 and 18:00 daily |
| `audiobook-periodicals-sync.service` | Service | Runs the sync script |

Timer configuration:
```ini
[Timer]
OnCalendar=*-*-* 06:00:00
OnCalendar=*-*-* 18:00:00
Persistent=true           # Run if missed (system was off)
RandomizedDelaySec=300    # Spread load across 5 minutes
```

### File Locations

| File | Purpose |
|------|---------|
| `library/backend/api_modular/periodicals.py` | Flask Blueprint with API endpoints |
| `library/web-v2/periodicals.html` | Reading Room UI |
| `library/web-v2/css/periodicals.css` | Dedicated CSS module |
| `scripts/sync-periodicals-index` | Sync script (Bash) |
| `systemd/audiobook-periodicals-sync.service` | Systemd service |
| `systemd/audiobook-periodicals-sync.timer` | Systemd timer |

For complete implementation details, see [Periodicals Guide](PERIODICALS.md).

---

## Systemd Services Reference

All systemd units are located in `systemd/` and installed to `/etc/systemd/system/`.

### Service Units

| Unit | Type | Purpose |
|------|------|---------|
| `audiobook-api.service` | Service | Flask REST API (Waitress server on port 5001) |
| `audiobook-proxy.service` | Service | HTTPS reverse proxy (port 8443 → 5001) |
| `audiobook-redirect.service` | Service | HTTP→HTTPS redirect (port 8081 → 8443) |
| `audiobook-converter.service` | Service | AAXC to Opus conversion daemon |
| `audiobook-mover.service` | Service | Staging to Library file mover |
| `audiobook-downloader.service` | Service | Audible download daemon |
| `audiobook-periodicals-sync.service` | Service | Periodicals metadata sync |
| `audiobook-upgrade-helper.service` | Service | Privileged operations helper (runs as root) |
| `audiobook-shutdown-saver.service` | Service | Saves staging to disk on system shutdown |

### Timer Units

| Unit | Schedule | Purpose |
|------|----------|---------|
| `audiobook-downloader.timer` | Configurable | Triggers download checks |
| `audiobook-periodicals-sync.timer` | 06:00, 18:00 | Syncs periodical metadata from Audible |

### Path Units

| Unit | Watches | Purpose |
|------|---------|---------|
| `audiobook-upgrade-helper.path` | `/var/lib/audiobooks/.control/upgrade-request` | Triggers helper on upgrade request |

### Target Unit

| Unit | Purpose |
|------|---------|
| `audiobook.target` | Groups all audiobook services for unified start/stop |

### tmpfiles.d Configuration

| File | Purpose |
|------|---------|
| `audiobooks-tmpfiles.conf` | Creates runtime directories on boot |

---

## Scripts Reference

All scripts are located in `scripts/` and installed to `/opt/audiobooks/scripts/`.

### Core Pipeline Scripts

| Script | Purpose | Service |
|--------|---------|---------|
| `download-new-audiobooks` | Downloads new purchases from Audible | audiobook-downloader |
| `convert-audiobooks-opus-parallel` | Parallel AAXC→Opus conversion | audiobook-converter |
| `move-staged-audiobooks` | Moves completed conversions to Library | audiobook-mover |
| `sync-periodicals-index` | Syncs periodical metadata from Audible API | audiobook-periodicals-sync |

### Service Control Scripts

| Script | Purpose |
|--------|---------|
| `audiobook-start` | Start all audiobook services |
| `audiobook-stop` | Stop all audiobook services |
| `audiobook-status` | Show service and timer status |
| `audiobook-enable` | Enable services for auto-start |
| `audiobook-disable` | Disable services from auto-start |

### Utility Scripts

| Script | Purpose |
|--------|---------|
| `audiobook-help` | Display help and usage information |
| `build-conversion-queue` | Build/rebuild conversion priority queue |
| `cleanup-stale-indexes` | Remove orphaned index entries |
| `copy-audiobook-metadata` | Copy metadata between audiobook files |
| `embed-cover-art.py` | Embed cover images into audio files |
| `fix-wrong-chapters-json` | Repair malformed chapter metadata |
| `monitor-audiobook-conversion` | Watch conversion progress in real-time |
| `audiobook-download-monitor` | Watch download progress |
| `audiobook-save-staging` | Manually save staging to persistent storage |
| `audiobook-save-staging-auto` | Auto-save staging (called by shutdown service) |

### Symlink Architecture

Wrapper scripts in `/usr/local/bin/` provide system-wide access:

```
/usr/local/bin/audiobook-*  →  /opt/audiobooks/scripts/*
```

**Key wrappers:**
- `audiobook-convert` → `convert-audiobooks-opus-parallel`
- `audiobook-download` → `download-new-audiobooks`
- `audiobook-move` → `move-staged-audiobooks`
- `audiobook-upgrade` → `upgrade.sh`
- `audiobook-migrate` → `migrate-api.sh`
- `audiobook-status` → `audiobook-status`
- `audiobook-help` → `audiobook-help`

---

## Installation Workflow

### System Installation Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SYSTEM INSTALLATION                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │     ./install.sh --system     │
                    └───────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
        ┌───────────────────┐           ┌───────────────────┐
        │   Check Ports     │           │  Check sudo       │
        │   5001, 8443,     │           │  access           │
        │   8081            │           │                   │
        └─────────┬─────────┘           └─────────┬─────────┘
                  │                               │
                  └───────────────┬───────────────┘
                                  ▼
                    ┌───────────────────────────────┐
                    │   Create directories          │
                    │   • /opt/audiobooks/          │
                    │   • /opt/audiobooks/scripts/  │
                    │   • /etc/audiobooks/          │
                    │   • /var/lib/audiobooks/      │
                    │   • /var/log/audiobooks/      │
                    └───────────────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────────┐
                    │   Copy application files      │
                    │   • library/ → /opt/.../      │
                    │   • converter/ → /opt/.../    │
                    │   • scripts/ → /opt/.../      │
                    └───────────────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────────┐
                    │   Create symlinks             │
                    │   /usr/local/bin/audiobooks-* │
                    │        ↓ symlink ↓            │
                    │   /opt/audiobooks/scripts/*   │
                    └───────────────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────────┐
                    │   Create wrapper scripts      │
                    │   • audiobook-api            │
                    │   • audiobooks-web            │
                    │   • audiobooks-scan           │
                    │   • audiobooks-import         │
                    └───────────────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────────┐
                    │   Setup Python venv           │
                    │   /opt/audiobooks/library/    │
                    │   venv/                       │
                    └───────────────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────────┐
                    │   Generate SSL certificates   │
                    │   /etc/audiobooks/certs/      │
                    │   • server.crt                │
                    │   • server.key                │
                    └───────────────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────────┐
                    │   Install systemd services    │
                    │   • audiobook-api.service    │
                    │   • audiobook-proxy.service  │
                    │   • audiobook-converter      │
                    │   • audiobook-mover          │
                    │   • audiobook-upgrade-helper │
                    │     .service + .path          │
                    │   • audiobook.target         │
                    └───────────────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────────┐
                    │   Create config file          │
                    │   /etc/audiobooks/            │
                    │   audiobooks.conf             │
                    └───────────────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────────┐
                    │   Create backward-compat      │
                    │   symlink:                    │
                    │   /usr/local/lib/audiobooks   │
                    │        ↓ symlink ↓            │
                    │   /opt/audiobooks/lib/        │
                    └───────────────────────────────┘
                                  │
                                  ▼
                    ┌───────────────────────────────┐
                    │   Enable & start services     │
                    │   • systemctl enable          │
                    │     audiobook.target         │
                    │   • systemctl start           │
                    │     audiobook.target         │
                    │   • Verify services running   │
                    └───────────────────────────────┘
                                  │
                                  ▼
                         ┌───────────────┐
                         │   COMPLETE    │
                         └───────────────┘
```

**Note:** Wrapper scripts in `/usr/local/bin/` source configuration from `/opt/audiobooks/lib/audiobook-config.sh` (canonical path). The backward-compat symlink at `/usr/local/lib/audiobooks` ensures older scripts continue to work.

### User Installation Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            USER INSTALLATION                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │      ./install.sh --user      │
                    │         (no sudo)             │
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   Create directories          │
                    │   • ~/.local/lib/audiobooks/  │
                    │   • ~/.config/audiobooks/     │
                    │   • ~/Audiobooks/             │
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   Copy application files      │
                    │   to ~/.local/lib/audiobooks/ │
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   Create wrapper scripts      │
                    │   in ~/.local/bin/            │
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   Install systemd --user      │
                    │   services                    │
                    │   ~/.config/systemd/user/     │
                    └───────────────────────────────┘
                                    │
                                    ▼
                         ┌───────────────┐
                         │   COMPLETE    │
                         └───────────────┘
```

---

## Upgrade Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              UPGRADE WORKFLOW                               │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌───────────────────────────────┐
                    │   audiobook-upgrade          │
                    │        OR                     │
                    │   upgrade.sh --from-project   │
                    │   upgrade.sh --from-github    │
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   Compare versions            │
                    │   Project vs Installed        │
                    └───────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
             Same version?                   Newer available?
                    │                               │
                    ▼                               ▼
           ┌───────────────┐              ┌───────────────────┐
           │  Exit (noop)  │              │  Stop services    │
           └───────────────┘              │  (automatic)      │
                                          └─────────┬─────────┘
                                                    │
                                                    ▼
                                          ┌───────────────────┐
                                          │  Create backup    │
                                          │  /opt/audiobooks  │
                                          │  .backup.YYYYMMDD │
                                          └─────────┬─────────┘
                                                    │
                                                    ▼
                    ┌─────────────────────────────────────────────────────────┐
                    │                    UPDATE COMPONENTS                    │
                    ├─────────────────────────────────────────────────────────┤
                    │                                                         │
                    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
                    │  │  scripts/   │  │  library/   │  │  converter/ │     │
                    │  │  (rsync)    │  │  (rsync)    │  │  (rsync)    │     │
                    │  │             │  │             │  │             │     │
                    │  │  Excludes:  │  │  Excludes:  │  │             │     │
                    │  │  (none)     │  │  • venv/    │  │             │     │
                    │  │             │  │  • *.db     │  │             │     │
                    │  │             │  │  • certs/   │  │             │     │
                    │  └─────────────┘  └─────────────┘  └─────────────┘     │
                    │                                                         │
                    └─────────────────────────────────────────────────────────┘
                                                    │
                                                    ▼
                                          ┌───────────────────┐
                                          │  Update VERSION   │
                                          │  file             │
                                          └─────────┬─────────┘
                                                    │
                                                    ▼
                                          ┌───────────────────┐
                                          │  Restart services │
                                          │  (automatic)      │
                                          └─────────┬─────────┘
                                                    │
                                                    ▼
                                          ┌───────────────────┐
                                          │  Verify health    │
                                          │  endpoints        │
                                          └─────────┬─────────┘
                                                    │
                                                    ▼
                                           ┌───────────────┐
                                           │   COMPLETE    │
                                           └───────────────┘

Note: Symlinks in /usr/local/bin/ automatically point to updated scripts
      because they reference /opt/audiobooks/scripts/ (canonical location)
```

---

## Migration Workflow

The migration workflow switches between API architectures (monolithic ↔ modular):

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             MIGRATION WORKFLOW                              │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌───────────────────────────────┐
                    │   audiobooks-migrate          │
                    │        --to modular           │
                    │        --to monolithic        │
                    │        --check                │
                    └───────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   Detect current architecture │
                    │   (check api_server.py)       │
                    └───────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
               --check?                        --to <arch>?
                    │                               │
                    ▼                               ▼
           ┌───────────────┐              ┌───────────────────┐
           │  Display      │              │  Validate target  │
           │  current arch │              │  architecture     │
           │  and exit     │              │  exists           │
           └───────────────┘              └─────────┬─────────┘
                                                    │
                                                    ▼
                                          ┌───────────────────┐
                                          │  Stop API service │
                                          └─────────┬─────────┘
                                                    │
                                                    ▼
                                          ┌───────────────────┐
                                          │  Update wrapper   │
                                          │  scripts:         │
                                          │                   │
                                          │  monolithic:      │
                                          │    → api.py       │
                                          │                   │
                                          │  modular:         │
                                          │    → api_server.py│
                                          └─────────┬─────────┘
                                                    │
                                                    ▼
                                          ┌───────────────────┐
                                          │  Restart services │
                                          └─────────┬─────────┘
                                                    │
                                                    ▼
                                          ┌───────────────────┐
                                          │  Verify API       │
                                          │  responding       │
                                          └─────────┬─────────┘
                                                    │
                                                    ▼
                                           ┌───────────────┐
                                           │   COMPLETE    │
                                           └───────────────┘

Architecture Comparison:
┌─────────────────────────────────────────────────────────────────────────────┐
│  MONOLITHIC (api.py)              │  MODULAR (api_modular/)                 │
├───────────────────────────────────┼─────────────────────────────────────────┤
│  • Single file (~2000 lines)      │  • Multiple modules (~200-400 lines)    │
│  • Simple deployment              │  • Better code organization             │
│  • Battle-tested                  │  • Easier parallel development          │
│  • All tests pass                 │  • Foundation for microservices         │
└───────────────────────────────────┴─────────────────────────────────────────┘
```

---

## Storage Layout

### Default Locations

| Component | System Install | User Install | Environment Variable |
|-----------|----------------|--------------|---------------------|
| **Application** | `/opt/audiobooks/` | `~/.local/lib/audiobooks/` | `AUDIOBOOKS_HOME` |
| **Library** | `/srv/audiobooks/Library/` | `~/Audiobooks/Library/` | `AUDIOBOOKS_LIBRARY` |
| **Sources** | `/srv/audiobooks/Sources/` | `~/Audiobooks/Sources/` | `AUDIOBOOKS_SOURCES` |
| **Supplements** | `/srv/audiobooks/Supplements/` | `~/Audiobooks/Supplements/` | `AUDIOBOOKS_SUPPLEMENTS` |
| **Database** | `/var/lib/audiobooks/db/audiobooks.db` | `~/.local/share/audiobooks/audiobooks.db` | `AUDIOBOOKS_DATABASE` |
| **Covers** | `/srv/audiobooks/.covers/` | `~/Audiobooks/.covers/` | `AUDIOBOOKS_COVERS` |
| **Logs** | `/var/log/audiobooks/` | `~/Audiobooks/logs/` | `AUDIOBOOKS_LOGS` |
| **Config** | `/etc/audiobooks/audiobooks.conf` | `~/.config/audiobooks/audiobooks.conf` | - |
| **Certs** | `/etc/audiobooks/certs/` | `~/.config/audiobooks/certs/` | `AUDIOBOOKS_CERTS` |

### Configuration Priority

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CONFIGURATION PRIORITY                               │
│                     (later sources override earlier)                        │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────────┐
  │  Built-in       │  ◀── Lowest priority
  │  Defaults       │      (hardcoded in config.py)
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │  /etc/audiobooks│      System-wide config
  │  /audiobooks.   │      (affects all users)
  │  conf           │
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │  ~/.config/     │      Per-user overrides
  │  audiobooks/    │
  │  audiobooks.conf│
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │  Environment    │  ◀── Highest priority
  │  Variables      │      (AUDIOBOOKS_*)
  └─────────────────┘
```

---

## Storage Recommendations

### By Component Type

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       STORAGE TIER RECOMMENDATIONS                          │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│  TIER 1: HIGH-PERFORMANCE (NVMe SSD)                                        │
│  ═══════════════════════════════════                                        │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐                                  │
│  │    DATABASE     │  │   APPLICATION   │                                  │
│  │                 │  │   (optional)    │                                  │
│  │ • SQLite DB     │  │                 │                                  │
│  │ • Random I/O    │  │ • Fast startup  │                                  │
│  │ • Low latency   │  │ • Script exec   │                                  │
│  │   critical      │  │                 │                                  │
│  └─────────────────┘  └─────────────────┘                                  │
│                                                                             │
│  Recommended: NVMe SSD with low queue depth latency                        │
│  Capacity: 1-10 GB sufficient                                              │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  TIER 2: BALANCED (SATA SSD or Fast HDD)                                    │
│  ═══════════════════════════════════════                                    │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │     COVERS      │  │      LOGS       │  │   SUPPLEMENTS   │             │
│  │                 │  │                 │  │                 │             │
│  │ • Random read   │  │ • Append-only   │  │ • Sequential    │             │
│  │ • Small files   │  │ • Low priority  │  │ • Occasional    │             │
│  │ • Cacheable     │  │                 │  │                 │             │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘             │
│                                                                             │
│  Recommended: SATA SSD or high-quality HDD                                  │
│  Capacity: 10-50 GB typical                                                 │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  TIER 3: BULK STORAGE (HDD / HDD RAID)                                      │
│  ═════════════════════════════════════                                      │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐       │
│  │                         AUDIOBOOK DATA                          │       │
│  │                                                                 │       │
│  │  ┌─────────────────┐              ┌─────────────────┐          │       │
│  │  │     LIBRARY     │              │     SOURCES     │          │       │
│  │  │                 │              │                 │          │       │
│  │  │ • Opus files    │              │ • AAXC files    │          │       │
│  │  │ • Sequential    │              │ • Sequential    │          │       │
│  │  │   streaming     │              │   read (convert)│          │       │
│  │  │ • Large files   │              │ • Write once    │          │       │
│  │  │   (50-500 MB)   │              │                 │          │       │
│  │  └─────────────────┘              └─────────────────┘          │       │
│  │                                                                 │       │
│  └─────────────────────────────────────────────────────────────────┘       │
│                                                                             │
│  Recommended: HDD RAID (RAID0 for speed, RAID1/5/6 for redundancy)         │
│  Capacity: 500 GB - 10+ TB depending on library size                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### I/O Characteristics

| Component | Read Pattern | Write Pattern | Typical Size | IOPS Need |
|-----------|--------------|---------------|--------------|-----------|
| **Database** | Random, frequent | Random, moderate | 50-500 MB | **High** |
| **Library** | Sequential streaming | Rare (after conversion) | 100-500 MB/file | Low |
| **Sources** | Sequential (conversion) | Once (download) | 100-800 MB/file | Low |
| **Covers** | Random, cached | Once (extraction) | 50-500 KB/file | Medium |
| **Logs** | Rare | Append-only | Grows over time | Low |
| **Application** | Startup, occasional | Upgrades only | ~100 MB | Low |

### Example Configurations

#### Home Server (Budget)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  BUDGET HOME SERVER                                                         │
│  Single 1TB NVMe + 4TB HDD                                                  │
└─────────────────────────────────────────────────────────────────────────────┘

  NVMe (1TB):
    /                           # Root filesystem
    /var/lib/audiobooks/        # Database (on fast storage)
    /opt/audiobooks/            # Application

  HDD (4TB):
    /srv/audiobooks/            # All audiobook data
      ├── Library/              # Converted files
      ├── Sources/              # Original files
      ├── .covers/              # Cover art
      └── logs/                 # Application logs
```

#### Performance-Optimized

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  PERFORMANCE-OPTIMIZED                                                      │
│  NVMe + SATA SSD + HDD RAID                                                │
└─────────────────────────────────────────────────────────────────────────────┘

  NVMe (500GB):
    /var/lib/audiobooks/        # Database
    /opt/audiobooks/            # Application
    /srv/audiobooks/.covers/    # Cover art (symlinked)

  SATA SSD (1TB):
    /var/log/audiobooks/        # Logs
    /srv/audiobooks/Supplements # PDFs

  HDD RAID0 (8TB):
    /raid0/Audiobooks/          # AUDIOBOOKS_DATA
      ├── Library/              # Streaming source
      └── Sources/              # Original files
```

#### High-Availability

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  HIGH-AVAILABILITY                                                          │
│  NVMe RAID1 + HDD RAID6                                                    │
└─────────────────────────────────────────────────────────────────────────────┘

  NVMe RAID1 (mirrored):
    /var/lib/audiobooks/        # Database (redundant)
    /opt/audiobooks/            # Application

  HDD RAID6 (8-disk):
    /storage/audiobooks/        # All user data
      ├── Library/
      ├── Sources/
      ├── .covers/
      └── Supplements/
```

---

## Filesystem Recommendations

### Comparison Matrix

| Filesystem | Database | Audiobooks | Logs | Best For |
|------------|----------|------------|------|----------|
| **ext4** | ★★★★☆ | ★★★★☆ | ★★★★★ | General purpose, stability |
| **XFS** | ★★★★☆ | ★★★★★ | ★★★★☆ | Large files, streaming |
| **Btrfs** | ★★★☆☆ | ★★★★☆ | ★★★★☆ | Snapshots, compression |
| **ZFS** | ★★★★★ | ★★★★★ | ★★★★★ | Enterprise, data integrity |
| **F2FS** | ★★★★★ | ★★★☆☆ | ★★★★☆ | Flash optimization |

### Detailed Recommendations

#### ext4 (Recommended for Most Users)

```
Best for: General-purpose, maximum compatibility
Kernel support: All Linux kernels (stable since 2.6.28)

Recommended mount options:
  # Database/Application (NVMe/SSD)
  defaults,noatime,commit=60

  # Audiobook data (HDD)
  defaults,noatime,data=ordered

Tuning for SQLite (database partition):
  # Disable access time updates
  noatime

  # Increase commit interval for write batching
  commit=60

  # Enable barriers for data integrity
  barrier=1
```

#### XFS (Recommended for Large Audiobook Libraries)

```
Best for: Large files, high-throughput streaming, >1TB libraries
Kernel support: All Linux kernels (stable since 2.4)

Recommended mount options:
  # Audiobook data (HDD/RAID)
  defaults,noatime,logbufs=8,logbsize=256k

  # Enable reflinks if using XFS 5.1+ (kernel 5.1+)
  reflink=1  # (at mkfs time)

Tuning:
  # Increase log buffer for write performance
  logbufs=8,logbsize=256k

  # Disable access time
  noatime

  # For SSDs: enable discard
  discard
```

#### Btrfs (Recommended for Snapshot/Backup Workflows)

```
Best for: Snapshots, compression, flexible storage
Kernel support: Stable for single-disk since 3.10, RAID since 5.0+

⚠️  CAUTION for Database:
    Btrfs copy-on-write (CoW) can cause fragmentation with SQLite.
    Disable CoW for database directory:

    chattr +C /var/lib/audiobooks/

Recommended mount options:
  # Audiobook data (take advantage of compression)
  defaults,noatime,compress=zstd:3,space_cache=v2

  # Database (disable CoW)
  defaults,noatime,nodatacow  # Or use chattr +C on directory

Subvolume layout (recommended):
  @audiobooks          → /srv/audiobooks
  @audiobooks-db       → /var/lib/audiobooks  (nodatacow)
  @audiobooks-logs     → /var/log/audiobooks

Compression notes:
  - Opus audio files are already compressed; zstd provides minimal benefit
  - PDFs and logs benefit from compression
  - Use compress-force=zstd:1 for moderate compression with low overhead
```

#### ZFS (Recommended for Enterprise/Data Integrity)

```
Best for: Data integrity, enterprise deployments, large arrays
Kernel support: Via OpenZFS module (all modern kernels)

⚠️  NOTE: ZFS is not in mainline kernel; requires OpenZFS installation

Dataset layout:
  tank/audiobooks/library      compression=off, recordsize=1M
  tank/audiobooks/sources      compression=off, recordsize=1M
  tank/audiobooks/database     compression=lz4, recordsize=16K, sync=standard
  tank/audiobooks/covers       compression=lz4, recordsize=128K
  tank/audiobooks/logs         compression=lz4, recordsize=128K

Tuning for audiobook streaming:
  # Large recordsize for sequential streaming
  zfs set recordsize=1M tank/audiobooks/library

  # Standard recordsize for database (matches SQLite page size)
  zfs set recordsize=16K tank/audiobooks/database

  # Enable compression where beneficial
  zfs set compression=lz4 tank/audiobooks/logs
```

#### F2FS (Recommended for Flash-Only Systems)

```
Best for: All-flash systems, embedded devices, SSDs
Kernel support: Stable since 3.8

Recommended mount options:
  # SSD/NVMe
  defaults,noatime,background_gc=on,discard,no_heap,inline_xattr

Tuning:
  # Enable background garbage collection
  background_gc=on

  # Enable discard for TRIM support
  discard

  # Disable heap allocation for better random performance
  no_heap
```

### tmpfs and RAM-based Filesystem Considerations

If your `/tmp` or `/var` directories (or subdirectories) are mounted as **tmpfs** (RAM-based filesystems), additional configuration is required. tmpfs partitions are cleared on every reboot, but Audiobook-Manager expects certain directories to exist for inter-service communication.

#### tmpfs Overview

```
tmpfs (temporary filesystem):
  • Resides entirely in RAM (and swap if needed)
  • Contents are lost on reboot
  • Extremely fast I/O (no disk latency)
  • Common for /tmp, /var/run, sometimes /var/tmp

Benefits:
  • Reduces SSD/NVMe write wear
  • Near-instant file operations
  • Ideal for truly temporary data

Risks for Audiobook-Manager:
  • Inter-service directories disappear on reboot
  • Services fail to start if directories don't exist
  • Trigger files and FIFOs are lost
```

#### Required Directories

Audiobook-Manager uses these directories for runtime operations:

| Directory | Purpose | Filesystem Type |
|-----------|---------|-----------------|
| `/tmp/audiobook-staging` | In-progress downloads and conversions | tmpfs-safe (recreated at boot) |
| `/tmp/audiobook-triggers` | Inter-service completion signals | tmpfs-safe (recreated at boot) |
| `/var/lib/audiobooks/.control` | Privileged helper IPC | May be tmpfs |
| `/var/lib/audiobooks/.run` | Runtime locks, temp files, FIFOs | May be tmpfs |

**Note:** If `/var/lib` or `/var/lib/audiobooks` is on tmpfs, the `.control` and `.run` directories will also be cleared on reboot.

#### tmpfiles.d Configuration

The `audiobooks-tmpfiles.conf` file ensures these directories are recreated at boot:

```ini
# /etc/tmpfiles.d/audiobooks.conf

# Control directory for privileged helper communication
d /var/lib/audiobooks/.control 0755 audiobooks audiobooks -

# Runtime directory for locks, temp files, and FIFOs
d /var/lib/audiobooks/.run 0775 audiobooks audiobooks -

# Staging directory for in-progress downloads and conversions
d /tmp/audiobook-staging 0775 audiobooks audiobooks -

# Triggers directory for inter-service signaling
d /tmp/audiobook-triggers 0755 audiobooks audiobooks -
```

#### Setup and Verification

```bash
# Deploy tmpfiles.d configuration (if not installed automatically)
sudo cp systemd/audiobooks-tmpfiles.conf /etc/tmpfiles.d/audiobooks.conf

# Create directories immediately (without reboot)
sudo systemd-tmpfiles --create /etc/tmpfiles.d/audiobooks.conf

# Verify directories exist with correct ownership
for dir in /tmp/audiobook-staging /tmp/audiobook-triggers \
           /var/lib/audiobooks/.control /var/lib/audiobooks/.run; do
    stat -c '%U:%G %a %n' "$dir" 2>/dev/null || echo "MISSING: $dir"
done
```

#### Detecting tmpfs Issues

```bash
# Check if /tmp is tmpfs
mount | grep 'on /tmp type'
# Output like "tmpfs on /tmp type tmpfs" indicates tmpfs

# Check if /var or subdirectories are tmpfs
mount | grep '/var'
findmnt --target /var/lib/audiobooks

# Look for symptoms in journal
journalctl -u 'audiobook-*' --since today | \
    grep -E '(No such file|Read-only|Permission denied|ENOENT)'
```

#### Symptoms of Missing tmpfs Configuration

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| Services fail after reboot | Missing tmpfiles.d | Deploy `audiobooks-tmpfiles.conf` |
| "Read-only file system" errors | Trigger directory doesn't exist | Run `systemd-tmpfiles --create` |
| Converter stops mid-queue | Trigger files can't be written | Check `/tmp/audiobook-triggers` exists |
| Mover leaves files in staging | Can't signal completion | Verify directory permissions |
| API can't control services | `.control` directory missing | Check `/var/lib/audiobooks/.control` |

#### When to Keep /tmp as tmpfs

Despite the configuration overhead, keeping `/tmp` as tmpfs is often beneficial:

```
✓ KEEP tmpfs for /tmp if:
  • SSD/NVMe wear is a concern
  • System has adequate RAM (4GB+)
  • tmpfiles.d is properly configured
  • You understand the reboot implications

✗ CONSIDER persistent /tmp if:
  • System has limited RAM (<2GB)
  • Frequent reboots with in-flight conversions
  • Simpler configuration is preferred
```

### Kernel Version Compatibility

| Filesystem | Minimum Kernel | Recommended Kernel | Notes |
|------------|----------------|-------------------|-------|
| **ext4** | 2.6.28 | Any stable | Universal support |
| **XFS** | 2.4.x | 5.10+ | reflinks require 5.1+ |
| **Btrfs** | 3.10 (single) | 6.1+ | RAID5/6 stable in 5.0+ |
| **ZFS** | N/A (module) | Any with OpenZFS | Not in mainline |
| **F2FS** | 3.8 | 5.4+ | Compression in 5.6+ |
| **tmpfs** | 2.4+ | Any stable | RAM-based; requires tmpfiles.d |

---

## Kernel Compatibility

### Supported Kernel Versions

Audiobook-Manager is tested and supported on:

| Distribution Type | Kernel Range | Status |
|------------------|--------------|--------|
| **LTS Kernels** | 5.4, 5.10, 5.15, 6.1, 6.6 | ✅ Fully Supported |
| **Stable Kernels** | 6.8, 6.9, 6.10, 6.11 | ✅ Fully Supported |
| **Rolling Release** | 6.12+ (CachyOS, Arch, etc.) | ✅ Fully Supported |

### Distribution-Specific Notes

#### Enterprise/LTS Distributions

```
RHEL/Rocky/Alma 8.x:    Kernel 4.18 ✅ (with backports)
RHEL/Rocky/Alma 9.x:    Kernel 5.14 ✅
Ubuntu 20.04 LTS:       Kernel 5.4  ✅
Ubuntu 22.04 LTS:       Kernel 5.15 ✅ (or 6.5 HWE)
Ubuntu 24.04 LTS:       Kernel 6.8  ✅
Debian 11 (Bullseye):   Kernel 5.10 ✅
Debian 12 (Bookworm):   Kernel 6.1  ✅
```

#### Rolling Release Distributions

```
Arch Linux:             Latest stable  ✅
CachyOS:                Latest + patches ✅ (optimized for performance)
openSUSE Tumbleweed:    Latest stable  ✅
Fedora (current):       Recent stable  ✅
Gentoo:                 User choice    ✅
```

### Kernel Features Used

| Feature | Minimum Kernel | Used By |
|---------|----------------|---------|
| inotify | 2.6.13 | File watching |
| epoll | 2.5.44 | Network I/O |
| sendfile | 2.2 | Efficient file transfer |
| splice | 2.6.17 | Zero-copy I/O |
| io_uring | 5.1 | Async I/O (optional) |
| cgroups v2 | 4.5 | Container support |

### Performance Optimizations by Kernel

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     KERNEL PERFORMANCE FEATURES                             │
└─────────────────────────────────────────────────────────────────────────────┘

Kernel 5.4+:
  • Multi-queue block layer (blk-mq) mature
  • BFQ I/O scheduler recommended for HDDs

Kernel 5.10+:
  • io_uring optimizations
  • Better NVMe performance

Kernel 6.1+ (LTS):
  • Improved Btrfs stability
  • Better memory management
  • Enhanced BPF capabilities

Kernel 6.6+ (Current LTS):
  • Latest Btrfs RAID fixes
  • Improved I/O scheduling
  • Better container support

Kernel 6.12+ (Rolling):
  • Cutting-edge performance
  • Latest driver support
  • Experimental features

CachyOS Kernel Recommendations:
  • Use BORE or EEVDF scheduler for desktop responsiveness
  • Enable BFQ for HDD-based audiobook storage
  • Use mq-deadline for NVMe database storage
```

### I/O Scheduler Recommendations

```bash
# Check current scheduler
cat /sys/block/sda/queue/scheduler

# For HDD (audiobook storage): BFQ
echo bfq | sudo tee /sys/block/sda/queue/scheduler

# For NVMe (database): none or mq-deadline
echo none | sudo tee /sys/block/nvme0n1/queue/scheduler

# Persistent via udev rule (/etc/udev/rules.d/60-io-scheduler.rules):
# HDDs: BFQ for fair queuing during conversion
ACTION=="add|change", KERNEL=="sd[a-z]", ATTR{queue/rotational}=="1", \
    ATTR{queue/scheduler}="bfq"

# NVMe: none (hardware handles scheduling)
ACTION=="add|change", KERNEL=="nvme[0-9]*", ATTR{queue/rotational}=="0", \
    ATTR{queue/scheduler}="none"

# SATA SSDs: mq-deadline
ACTION=="add|change", KERNEL=="sd[a-z]", ATTR{queue/rotational}=="0", \
    ATTR{queue/scheduler}="mq-deadline"
```

---

## Quick Reference

### Environment Variables

```bash
# Core paths
export AUDIOBOOKS_HOME=/opt/audiobooks
export AUDIOBOOKS_DATA=/srv/audiobooks
export AUDIOBOOKS_LIBRARY=/srv/audiobooks/Library
export AUDIOBOOKS_SOURCES=/srv/audiobooks/Sources
export AUDIOBOOKS_DATABASE=/var/lib/audiobooks/db/audiobooks.db
export AUDIOBOOKS_COVERS=/srv/audiobooks/.covers

# Server settings
export AUDIOBOOKS_API_PORT=5001
export AUDIOBOOKS_WEB_PORT=8443
export AUDIOBOOKS_BIND_ADDRESS=0.0.0.0
```

### Common Commands

```bash
# Installation
./install.sh --system              # System install
./install.sh --user                # User install
./install.sh --uninstall           # Remove installation

# Upgrade
audiobook-upgrade                 # From GitHub
audiobook-upgrade --check         # Check for updates
upgrade.sh --from-project /path    # From local project

# Migration
audiobooks-migrate --check         # Show current architecture
audiobooks-migrate --to modular    # Switch to modular
audiobooks-migrate --to monolithic # Switch to monolithic

# Services
sudo systemctl start audiobook.target
sudo systemctl status audiobook-api
sudo systemctl restart audiobook-proxy
```

### Health Checks

```bash
# API health
curl -s http://localhost:5001/api/health

# Web interface
curl -sk https://localhost:8443/ -o /dev/null -w '%{http_code}\n'

# Database
sqlite3 /var/lib/audiobooks/db/audiobooks.db 'SELECT COUNT(*) FROM audiobooks;'

# Service status
systemctl status audiobook.target --no-pager
```

---

## Appendix: Storage Decision Tree

```
                                    START
                                      │
                                      ▼
                    ┌─────────────────────────────────┐
                    │  Is this the DATABASE?          │
                    └─────────────────────────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                   YES                                  NO
                    │                                   │
                    ▼                                   ▼
        ┌───────────────────┐           ┌─────────────────────────────────┐
        │  Place on NVMe    │           │  Is this AUDIOBOOK FILES?       │
        │  or fastest SSD   │           │  (Library/ or Sources/)         │
        │                   │           └─────────────────────────────────┘
        │  Filesystem:      │                           │
        │  ext4 or XFS      │           ┌───────────────┴───────────────┐
        │  (disable CoW     │          YES                              NO
        │   on Btrfs)       │           │                               │
        └───────────────────┘           ▼                               ▼
                                ┌───────────────────┐   ┌───────────────────────┐
                                │  Place on bulk    │   │  Is this COVERS or    │
                                │  storage (HDD     │   │  SUPPLEMENTS?         │
                                │  RAID preferred)  │   └───────────────────────┘
                                │                   │               │
                                │  Filesystem:      │   ┌───────────┴───────────┐
                                │  XFS (large files)│  YES                      NO
                                │  or ext4          │   │                       │
                                └───────────────────┘   ▼                       ▼
                                                ┌───────────────┐   ┌───────────────┐
                                                │  Balanced     │   │  LOGS or      │
                                                │  storage:     │   │  APPLICATION  │
                                                │  SATA SSD     │   │               │
                                                │  or NVMe      │   │  Any storage  │
                                                │               │   │  tier is fine │
                                                │  Filesystem:  │   │               │
                                                │  Any          │   │  ext4 for     │
                                                └───────────────┘   │  simplicity   │
                                                                    └───────────────┘
```

---

*Document Version: 3.10.1*
*Last Updated: 2026-01-14*
