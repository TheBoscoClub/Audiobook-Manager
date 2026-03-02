# UI/UX Fixes Design — 2026-03-02

## Problem Statement

Three UI/UX issues reported by test users and verified by maintainer:

1. **Double-click Play bug**: First click appears to perform a full refresh; second click actually plays audio. Affects both mobile and desktop.
2. **Missing info on compact mobile icons**: On mobile (≤480px), book cards are 40x40px icons with title/author only. No way to view narrator, format, duration, or progress.
3. **Desktop CSS leak from mobile changes**: Landscape media queries lack width constraints, potentially matching desktop windows. Minor fluid sizing changes affect all viewports.

## Fix 1: Double-Click Play Bug

### 1a. Dev Caddyfile

**File**: `dev/Caddyfile`

Two line changes:
- `try_files {path} /index.html` → `try_files {path} /shell.html`
- `X-Frame-Options "DENY"` → `X-Frame-Options "SAMEORIGIN"`

**Why**: The shell+iframe architecture requires `shell.html` as the entry point. Serving `index.html` directly means `inIframe` is false, causing Play to hard-redirect to `shell.html` (perceived as a refresh). `X-Frame-Options DENY` blocks same-origin framing entirely.

### 1b. shellPlay() Intent Preservation

**File**: `library/web-v2/js/library.js`

Current behavior when `!inIframe`:
```javascript
window.location.href = 'shell.html';  // loses play intent
```

New behavior:
```javascript
sessionStorage.setItem('pendingPlay', JSON.stringify(book));
window.location.href = `shell.html?autoplay=${encodeURIComponent(book.bookId || book.id)}`;
```

**File**: `library/web-v2/js/shell.js`

In `DOMContentLoaded` handler, after creating `shellPlayer`:
```javascript
const params = new URLSearchParams(window.location.search);
const autoplayId = params.get('autoplay');
if (autoplayId) {
    const pending = sessionStorage.getItem('pendingPlay');
    if (pending) {
        sessionStorage.removeItem('pendingPlay');
        shellPlayer.playBook(JSON.parse(pending), false);
    }
    history.replaceState(null, '', window.location.pathname);
}
```

### 1c. Overlay Player Bar (No Layout Shift)

**File**: `library/web-v2/css/shell.css`

Remove the iframe resize rule:
```css
/* REMOVE */
body.player-active #content-frame {
    height: calc(100% - var(--player-height));
}
```

The iframe stays at `height: 100%` always. The player bar (already `position: fixed; z-index: 9999`) overlays content at the bottom.

**Content padding**: shell.js sends a `playerVisible` message to the iframe. library.js listens and adds `padding-bottom: 80px` (or 100px on mobile) to the page body so bottom content isn't hidden behind the player.

## Fix 2: Mobile Info Detail Modal

**File**: `library/web-v2/js/library.js`

Add `showBookDetail(bookId)` function that creates a bottom-sheet modal with:
- Larger cover art (~120px)
- Title, author, narrator
- Format, quality, duration
- Progress bar (if in progress)
- Play, Resume, Download buttons (full-size)
- Editions badge (if applicable)
- PDF supplement badge (if applicable)

**Activation**: Card-level click handler on `.book-card` (only active at compact viewports). The Play button's existing `event.stopPropagation()` prevents it from triggering the modal — quick-play still works directly.

**Detection**: Check `window.matchMedia('(max-width: 480px)')` or landscape compact queries at click time, so resizing between mobile/desktop behaves correctly.

**File**: `library/web-v2/css/modals.css` (or `library.css`)

Bottom-sheet modal styles:
- Slides up from bottom on mobile
- Semi-transparent backdrop
- Art Deco styling consistent with existing modals
- Close via backdrop tap, swipe down, or X button

## Fix 3: Landscape Media Query Safety

**File**: `library/web-v2/css/responsive.css`

Add `max-width` constraints to prevent desktop matching:

| Section | Current | Fixed |
|---------|---------|-------|
| D (line 233) | `(orientation: landscape) and (max-height: 500px)` | Add `and (max-width: 960px)` |
| D2 (line 373) | `(orientation: landscape) and (max-height: 700px)` | Add `and (max-width: 1024px)` |

Width caps ensure landscape phones (≤900px wide) match but desktop windows (≥1200px wide) don't.

## Files Changed

| File | Changes |
|------|---------|
| `dev/Caddyfile` | try_files + X-Frame-Options |
| `library/web-v2/js/library.js` | shellPlay() redirect with intent, card tap handler, showBookDetail() |
| `library/web-v2/js/shell.js` | autoplay param check on DOMContentLoaded, playerVisible message |
| `library/web-v2/css/shell.css` | Remove iframe resize rule |
| `library/web-v2/css/responsive.css` | Add max-width to landscape queries |
| `library/web-v2/css/modals.css` | Bottom-sheet modal styles |

## Testing

- **Desktop**: Verify landscape queries don't trigger on 1080p/1440p monitors
- **Desktop**: Verify Play button works on first click (no redirect, no layout shift)
- **Desktop**: Verify direct `index.html` access redirects to shell with autoplay
- **Mobile (≤480px)**: Verify compact cards show, tapping card opens detail modal
- **Mobile**: Verify Play button on compact card works (quick-play, no modal)
- **Mobile**: Verify player bar overlays without layout shift
- **Dev Caddy**: Verify iframe loads correctly with SAMEORIGIN header
