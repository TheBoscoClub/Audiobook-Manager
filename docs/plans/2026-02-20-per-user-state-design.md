# Per-User State & Library Experience — Design Document

**Date:** 2026-02-20
**Status:** Approved
**Authors:** Bosco (concept & direction), Claude (architecture & implementation)

## Overview

Transform The Library from a shared-state application into a multi-user experience where each user has their own listening positions, history, download records, and personalized library view. Remove Audible position sync in favor of local-only per-user tracking.

## Requirements

1. **Per-user position tracking** — each user resumes at THEIR position, independently
2. **Per-user listening history** — what you listened to and when
3. **Per-user download tracking** — what you downloaded from the web UI and when (recorded on completion only)
4. **My Library tab** — user's engaged books alongside the full Browse All view
5. **Admin audit view** — Back Office section showing who listened to/downloaded what, when
6. **New Books marquee** — Art Deco neon ticker announcing new audiobooks since user's last dismissal
7. **About The Library page** — credits, attributions, version, links
8. **Remove Audible position sync** — local-only, per-user tracking replaces it
9. **Help & tutorial updates** — cover all new features
10. **Concurrent access** — multiple users can listen to the same book simultaneously

## Storage: Extend Auth Database (SQLCipher)

All per-user data lives in the encrypted auth database. User listening habits are personal data that deserves encryption at rest. The library database remains the catalog (book metadata). Cross-DB joins are handled at the application layer.

### New Tables

```sql
-- Listening session history
CREATE TABLE IF NOT EXISTS user_listening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    position_start_ms INTEGER NOT NULL DEFAULT 0,
    position_end_ms INTEGER,
    duration_listened_ms INTEGER
);
CREATE INDEX idx_ulh_user ON user_listening_history(user_id);
CREATE INDEX idx_ulh_audiobook ON user_listening_history(audiobook_id);
CREATE INDEX idx_ulh_started ON user_listening_history(started_at);

-- Download completion records
CREATE TABLE IF NOT EXISTS user_downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    downloaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    file_format TEXT
);
CREATE INDEX idx_ud_user ON user_downloads(user_id);
CREATE INDEX idx_ud_audiobook ON user_downloads(audiobook_id);

-- Per-user preferences (new books dismissal, future settings)
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    new_books_seen_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### Existing Table (no changes)

```sql
-- Already exists in auth DB
user_positions (user_id, audiobook_id, position_ms, updated_at)
```

`audiobook_id` is TEXT matching the library database's book identifiers. No foreign key across databases — the library DB is the source of truth for book metadata, resolved at query time.

## API Changes

### Remove (Audible sync)

| Endpoint | Reason |
|----------|--------|
| `POST /api/position/sync/<id>` | Audible sync removed |
| `POST /api/position/sync-all` | Audible sync removed |
| `GET /api/position/syncable` | Audible sync removed |

### Modify

| Endpoint | Change |
|----------|--------|
| `GET /api/position/<id>` | Always per-user when auth enabled |
| `PUT /api/position/<id>` | Always per-user when auth enabled; creates/updates listening history entry |

### New Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/user/history` | GET | user | Paginated listening history |
| `/api/user/downloads` | GET | user | Download history |
| `/api/user/library` | GET | user | Books user has interacted with (My Library) |
| `/api/user/downloads/<id>/complete` | POST | user | Record completed download |
| `/api/user/new-books` | GET | user | Books added since user's last dismissal |
| `/api/user/new-books/dismiss` | POST | user | Update new_books_seen_at timestamp |
| `/api/admin/activity` | GET | admin | Filtered activity log (user, book, date, type) |
| `/api/admin/activity/stats` | GET | admin | Summary stats (top books, active users, totals) |

All standard REST — JSON in, JSON out, session cookie auth.

## Frontend Changes

### Player (library.js PlaybackManager)

- Position saves continue to `PUT /api/position/<id>` every 15 seconds — now always per-user
- On play: signal session start (backend creates listening history entry)
- On pause/stop/close: send final position (backend closes history entry)
- Resume: compare localStorage vs API, use furthest ahead — API now returns per-user position
- Brief seeks (< 5 seconds playback) not recorded as history entries

### Download Flow

- Currently: direct file link (`<a href>`)
- New: JS-driven fetch/blob download. On completion, browser triggers Save As and JS calls `POST /api/user/downloads/<id>/complete`. Cancelled/failed downloads are not recorded.

### My Library Tab

- New tab alongside existing library view
- Shows only books user has listened to or downloaded
- Each card shows: progress bar with percentage and time (`2h 15m / 8h 30m — 26%`), last listened date, download date if applicable
- Sorted by most recently interacted with

### Library View Enhancements

- Books user has engaged with show a clearly visible progress bar (not subtle — readable at a glance) with percentage/time text
- Books with no interaction look the same as today

### New Books Marquee

- Art Deco neon ticker styled after 1930s Times Square Motograph news signs
- Desktop: horizontally scrolling across the header area
- Mobile: wraps around the viewport — across the top and down both sides, like a theater marquee framing the content
- Appears when books have been added since user's last dismissal
- Clicking the marquee or associated button shows the new additions and dismisses
- Hidden when no new books — clean header
- Per-user via `new_books_seen_at` in `user_preferences`

### About The Library Page

- Accessible from the Help button menu
- Concept credit: Bosco
- Joint authorship: Bosco & Claude
- Third-party attributions: ffmpeg, SQLCipher, Flask, and other tools used
- Current version number and date
- Link to the full README.md
- Link to the GitHub repository

### Help & Tutorial Updates

- New sections covering: My Library, progress tracking, download history, new books marquee
- Tutorial steps added for the new features
- About The Library accessible from Help menu

## Audible Sync Removal

**Remove:**
- Audible sync functions in `position_sync.py` (keep local position get/save)
- Three sync API endpoints
- Audible sync UI elements in frontend
- Stop writing to `audible_position_ms`, `audible_position_updated`, `position_synced_at` columns

**Keep:**
- Audible downloader service (downloads new audiobooks — completely separate system)
- `audible` library dependency (used by downloader)
- Columns can remain in library DB schema (just unused) to avoid migration complexity

**Documentation:**
- Rewrite `docs/POSITION_SYNC.md` to document per-user local position system
- Update `docs/ARCHITECTURE.md`

## Concurrent Access & Edge Cases

| Scenario | Behavior |
|----------|----------|
| Two users listening to same book | Each has own position row — no conflict |
| Same user on multiple devices | Last-write-wins on position; API is authority, localStorage is cache |
| Auth disabled | Falls back to global behavior; My Library tab and per-user features hidden |
| User deletion | Cascade delete all user_positions, user_listening_history, user_downloads, user_preferences |
| Brief accidental play (< 5s) | Not recorded as listening history |

## Not Doing

- No global position migration (users start fresh)
- No Audible position sync replacement
- No mobile app considerations
- No offline/PWA support
