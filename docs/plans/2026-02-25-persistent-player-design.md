# Persistent Player, My Library Fix & Audible Cleanup — Design

**Date**: 2026-02-25
**Version**: 6.6.7+
**Status**: Approved (all sections)

## Problem Statement

Three issues to address:

1. **Player dies on navigation**: The `<audio>` element lives in `index.html`. Navigating to `utilities.html`, `admin.html`, `help.html`, etc. destroys it — playback stops and position resets to zero. Users want truly uninterrupted audio across all page navigation.

2. **My Library is empty**: The "My Library" tab shows no books for any user (production and test environments). Root cause: `savePositionToAPI()` and `getPositionFromAPI()` in `PlaybackManager` (library.js:2937, 2953) omit `credentials: 'include'` from fetch calls. Session cookie is not sent, so the server sees unauthenticated requests. No per-user position data is saved, no listening history is created, and My Library has nothing to display.

3. **Dead Audible sync code**: The backend Audible sync endpoint was removed, but ~30 references remain in `library.js` — `startAudibleSyncTimer()`, `stopAudibleSyncTimer()`, `syncWithAudible()`, and associated properties. This is dead code calling a non-existent endpoint.

## Constraints

- **No SPA rewrite** — the multi-page HTML architecture stays as-is
- **Zero audio interruption** — no gap, no silence, continuous playback during navigation
- **Auth pages stay standalone** — login, register, claim, verify are not in the shell
- **Same-origin framing only** — `X-Frame-Options: SAMEORIGIN` (approved)

## Design

### Section 1: Shell Page Layout

A new `shell.html` serves as the outer page for all authenticated content. It contains:

```
┌──────────────────────────────────────────────────┐
│  shell.html (parent)                             │
│                                                  │
│  ┌──────────────────────────────────────────┐    │
│  │  <iframe id="content">                   │    │
│  │    Loads: index.html, utilities.html,    │    │
│  │    admin.html, help.html, about.html,    │    │
│  │    contact.html                          │    │
│  └──────────────────────────────────────────┘    │
│                                                  │
│  ┌──────────────────────────────────────────┐    │
│  │  Player Bar (hidden until first play)    │    │
│  │  ▶ Title — Author    ═══════●═══  12:34  │    │
│  │  Now Playing: "Book Title" by Author     │    │
│  └──────────────────────────────────────────┘    │
└──────────────────────────────────────────────────┘
```

**Key decisions:**
- Player bar is fixed at the bottom of the viewport, hidden until a book is played
- When no player is active, iframe takes 100% of the viewport
- When player is visible, iframe shrinks to make room (no overlap)
- Login flow redirects to `shell.html` instead of `index.html`
- `shell.html` default iframe src is `index.html`
- Navigation inside iframe uses regular `<a href>` links (`<base target="_self">`)
- Auth-related links (logout, etc.) use `target="_top"` to break out of the iframe

**Pages in shell** (post-auth): `index.html`, `utilities.html`, `admin.html`, `help.html`, `about.html`, `contact.html`

**Pages standalone** (no shell): `login.html`, `register.html`, `claim.html`, `verify.html`, `401.html`, `403.html`

### Section 2: Shell-Iframe Communication

The shell and iframe content communicate via `postMessage` API (same-origin).

**Components that move to shell.html:**
- `AudioPlayer` class (currently in library.js)
- `PlaybackManager` class (currently in library.js)
- `<audio>` HTML element

**Message types (iframe → shell):**

| Message | Payload | Purpose |
|---------|---------|---------|
| `play` | `{ bookId, fileId, title, author, coverUrl }` | Start playing a book |
| `pause` | `{}` | Pause playback |
| `resume` | `{}` | Resume playback |
| `seek` | `{ positionSeconds }` | Seek to position |
| `getPlayerState` | `{}` | Request current player state |

**Message types (shell → iframe):**

| Message | Payload | Purpose |
|---------|---------|---------|
| `playerState` | `{ playing, bookId, title, author, position, duration }` | Player state update |
| `playerClosed` | `{}` | Player was closed |

**Implementation notes:**
- Shell listens for messages with `window.addEventListener('message', ...)`
- Iframe content posts via `window.parent.postMessage(...)`
- Origin validation on all messages (same-origin check)
- Shell sends periodic `playerState` updates so iframe content can show "Now Playing" indicators

### Section 3: Security Header Changes

Current headers in `library/backend/api_modular/core.py`:

| Header | Current (line 48) | New |
|--------|-------------------|-----|
| `X-Frame-Options` | `DENY` | `SAMEORIGIN` |
| CSP `frame-ancestors` | `'none'` (line 57) | `'self'` |
| CSP `frame-src` | not set | `'self'` |

These changes allow same-origin iframe embedding while preventing cross-origin framing (clickjacking protection maintained).

### Section 4: My Library Fix & Audible Cleanup

**My Library fix** — add `credentials: 'include'` to three PlaybackManager fetch calls:

| Method | Line | Fix |
|--------|------|-----|
| `savePositionToAPI()` | 2939 | Add `credentials: 'include'` to fetch options |
| `getPositionFromAPI()` | 2955 | Add `credentials: 'include'` to fetch options |
| `syncWithAudible()` | 3013 | Will be removed entirely (see below) |

**Dead Audible sync removal** — remove from `library.js`:

| Item | Lines | Action |
|------|-------|--------|
| `audibleSyncInterval` property | 2036 | Remove |
| `audibleSyncDelayMs` property | 2037 | Remove |
| `startAudibleSyncTimer()` method | 2319-2337 | Remove |
| `stopAudibleSyncTimer()` method | 2342-2346 | Remove |
| `syncWithAudible()` method | 3010-3035 | Remove |
| Call in `play()` | 2300-2301 | Remove |
| Call in `close()` | 2418-2419, 2430-2432 | Remove |
| Calls in `AudioPlayer.playAudiobook()` | 2334 | Remove |
| "furthest ahead" Audible comment | 2985 | Remove comment |

**No ID migration**: Existing stale IDs in the auth DB (from library reimport) are not migrated. The title denormalization fix from v6.6.7 already handles this — stored titles are used when library lookups miss. Once users play books again with the credentials fix, new correct IDs will be stored.

## Testing Strategy

| Test | Type | Where |
|------|------|-------|
| Shell page loads, iframe displays index.html | Integration | VM |
| Navigation within iframe works (all pages) | Integration | VM |
| Player persists across iframe navigation | Integration | VM |
| Play, pause, seek via postMessage | Integration | VM |
| Player bar hides/shows correctly | Integration | VM |
| Auth links break out of iframe (`target="_top"`) | Integration | VM |
| My Library populates after position saves | Integration | VM |
| `credentials: 'include'` present on all PlaybackManager fetches | Unit | Dev |
| No Audible sync references remain in frontend | Unit | Dev |
| Security headers correct (SAMEORIGIN, frame-ancestors 'self') | Unit | Dev |
| X-Frame-Options blocks cross-origin framing | Security | VM |

## File Change Summary

**New files:**
- `library/web-v2/shell.html` — shell page with iframe + player bar
- `library/web-v2/js/shell.js` — shell-side player logic, postMessage handling
- `library/web-v2/css/shell.css` — shell layout styles

**Modified files:**
- `library/web-v2/js/library.js` — remove AudioPlayer/PlaybackManager (move to shell), remove Audible sync dead code, add postMessage bridge for play commands, fix credentials
- `library/backend/api_modular/core.py` — security header changes (lines 48, 57)
- `library/backend/api_modular/auth_routes.py` — login redirect: `index.html` → `shell.html`
- All content HTML files (`index.html`, `utilities.html`, `admin.html`, `help.html`, `about.html`, `contact.html`) — ensure navigation links work within iframe context
- `Dockerfile` — `COPY library/web-v2 /app/web` already covers new files (no change needed)
