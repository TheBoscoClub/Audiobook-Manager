# Periodicals Subsystem Architecture

## Overview

The Periodicals feature provides a "reading room" interface for episodic Audible content (podcasts, newspapers, meditation series) that is skipped by default but available for selective download.

**Design Principles:**
1. Security and privacy first
2. KISS (Keep It Simple)
3. Operational efficiency

## Technology Stack

| Component | Technology | Rationale |
|-----------|------------|-----------|
| UI Interactions | HTMX | Declarative, Flask-native, minimal JS attack surface |
| Real-time Updates | SSE | One-way (read-only), browser-native, no library |
| Styling | Modular CSS | Consistent with existing app |
| Backend | Flask + SQLite | Existing stack |
| Sync Service | systemd timer | Reliable, logged, manageable |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PERIODICALS SUBSYSTEM                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐   │
│  │   Web Frontend  │     │   API Backend   │     │  Sync Service   │   │
│  │  periodicals.   │────▶│  /api/v1/       │◀────│  systemd timer  │   │
│  │  html + css     │     │  periodicals/*  │     │  (twice daily)  │   │
│  │  + htmx         │     │                 │     │                 │   │
│  └────────┬────────┘     └────────┬────────┘     └────────┬────────┘   │
│           │                       │                       │             │
│           │ SSE                   │ SQLite                │ Audible API │
│           ▼                       ▼                       ▼             │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐   │
│  │  Status Stream  │     │   periodicals   │     │  Skip List +    │   │
│  │  /api/v1/       │     │   table (new)   │     │  Index Cache    │   │
│  │  sync/status    │     │                 │     │                 │   │
│  └─────────────────┘     └─────────────────┘     └─────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Database Schema

```sql
-- New table for periodical content index
CREATE TABLE IF NOT EXISTS periodicals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_asin TEXT NOT NULL,           -- Parent podcast/series ASIN
    child_asin TEXT,                      -- Individual episode ASIN (nullable for parent)
    title TEXT NOT NULL,
    episode_title TEXT,                   -- Episode-specific title
    episode_number INTEGER,
    author TEXT,
    narrator TEXT,
    runtime_minutes INTEGER,
    release_date TEXT,
    description TEXT,
    cover_url TEXT,
    category TEXT,                        -- 'podcast', 'news', 'meditation', 'magazine'
    is_downloaded INTEGER DEFAULT 0,      -- 0=available, 1=downloaded
    download_requested INTEGER DEFAULT 0, -- 0=no, 1=queued
    last_synced TEXT,                     -- ISO timestamp
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(parent_asin, child_asin)
);

CREATE INDEX idx_periodicals_parent ON periodicals(parent_asin);
CREATE INDEX idx_periodicals_category ON periodicals(category);
CREATE INDEX idx_periodicals_downloaded ON periodicals(is_downloaded);
```

## API Endpoints

### Periodicals Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/periodicals` | List all periodical parents with episode counts |
| GET | `/api/v1/periodicals/<parent_asin>` | List episodes for a parent |
| GET | `/api/v1/periodicals/<parent_asin>/<child_asin>` | Episode details |
| POST | `/api/v1/periodicals/download` | Queue episodes for download |
| DELETE | `/api/v1/periodicals/download/<asin>` | Cancel queued download |
| GET | `/api/v1/periodicals/sync/status` | SSE stream for sync status |
| POST | `/api/v1/periodicals/sync/trigger` | Manually trigger sync |

### Request/Response Examples

**List Parents:**
```json
GET /api/v1/periodicals
{
  "periodicals": [
    {
      "parent_asin": "B08K56V638",
      "title": "Making Sense with Sam Harris",
      "category": "podcast",
      "episode_count": 476,
      "downloaded_count": 0,
      "queued_count": 0,
      "cover_url": "https://...",
      "last_synced": "2026-01-08T09:00:00Z"
    }
  ]
}
```

**Queue Downloads:**
```json
POST /api/v1/periodicals/download
{
  "asins": ["B08K56V638_EP001", "B08K56V638_EP002"],
  "priority": "normal"
}
```

## Sync Service

### systemd Timer (twice daily)

**File:** `/etc/systemd/system/audiobook-periodicals-sync.timer`
```ini
[Unit]
Description=Sync Audible periodicals index twice daily

[Timer]
OnCalendar=*-*-* 06:00:00
OnCalendar=*-*-* 18:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

**File:** `/etc/systemd/system/audiobook-periodicals-sync.service`
```ini
[Unit]
Description=Audible periodicals index sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=audiobooks
Group=audiobooks
ExecStart=/opt/audiobooks/scripts/sync-periodicals-index
TimeoutStartSec=600
StandardOutput=journal
StandardError=journal
```

### Sync Script Logic

```bash
#!/bin/bash
# /opt/audiobooks/scripts/sync-periodicals-index

# 1. Read skip list to get parent ASINs
# 2. For each parent, query Audible API for children
# 3. Upsert into periodicals table
# 4. Update last_synced timestamp
# 5. Emit SSE events for progress
```

## Frontend Components

### Page Structure (periodicals.html)

```
┌─────────────────────────────────────────────────────────────────┐
│ Header: "Reading Room" + Sync Status Indicator                  │
├─────────────────────────────────────────────────────────────────┤
│ Category Tabs: [All] [Podcasts] [News] [Meditation] [Other]     │
├───────────────────────┬─────────────────────────────────────────┤
│                       │                                         │
│  Parent List          │  Episode List (HTMX partial)            │
│  - Making Sense       │  ┌─────────────────────────────────┐   │
│  - True Crime...      │  │ □ EP 476: Latest Episode        │   │
│  - American Scandal   │  │ □ EP 475: Previous Episode      │   │
│  - NYT Digest         │  │ ☑ EP 474: Selected              │   │
│                       │  │ ...                              │   │
│  [Sync Now]           │  └─────────────────────────────────┘   │
│                       │                                         │
│                       │  [Download Selected] [Select All New]   │
├───────────────────────┴─────────────────────────────────────────┤
│ Download Queue: 3 items pending | Progress: EP 474 downloading  │
└─────────────────────────────────────────────────────────────────┘
```

### CSS Module (periodicals.css)

Follows existing modular pattern:
- Reading room aesthetic (warm tones, paper textures)
- Card-based episode display
- Checkbox bulk selection
- Progress indicators
- Category color coding

## File Structure

```
library/
├── web-v2/
│   ├── periodicals.html          # New page
│   ├── css/
│   │   └── periodicals.css       # New CSS module
│   └── js/
│       └── periodicals.js        # Minimal JS (SSE handling)
├── backend/
│   └── api_modular/
│       └── periodicals.py        # New API module
scripts/
└── sync-periodicals-index        # Sync script
systemd/
├── audiobook-periodicals-sync.service
└── audiobook-periodicals-sync.timer
```

## Security Considerations

1. **Input Validation**: All ASINs validated against regex `^[A-Z0-9]{10}$`
2. **Rate Limiting**: Audible API calls throttled to avoid account issues
3. **SSE Authentication**: Same session auth as main app
4. **No Stored Credentials**: Uses existing audible-cli auth
5. **Download Isolation**: Queued downloads processed by existing converter service

## Implementation Phases

### Phase 1: Foundation
- [ ] Database schema migration
- [ ] API endpoints (list, details)
- [ ] Basic sync script

### Phase 2: UI
- [ ] periodicals.html page
- [ ] periodicals.css styling
- [ ] HTMX integration

### Phase 3: Downloads
- [ ] Download queue management
- [ ] Integration with converter service
- [ ] Progress tracking

### Phase 4: Polish
- [ ] SSE status stream
- [ ] Bulk selection UX
- [ ] Category filtering

## Version History

- v1.0.0 (2026-01-08): Initial architecture design
