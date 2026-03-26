# UI/UX Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix three UI/UX issues: double-click Play bug, missing info on compact mobile icons, and desktop CSS leak from landscape media queries.

**Architecture:** Four independent fixes across 6 files. No new dependencies. CSS changes use existing Art Deco design tokens. JS changes extend existing shell+iframe message protocol. No backend changes.

**Tech Stack:** Vanilla JS, CSS media queries, Caddy config

**XSS Safety Note:** The detail modal in Task 7 uses innerHTML with book data. All user-visible text fields (title, author, narrator) MUST be escaped via the existing `this.escapeHtml()` method before insertion. The code below does this correctly. Numeric fields (id, percentComplete) and server-controlled fields (cover_path, format) are safe. The existing `createBookCard()` method uses the same innerHTML + escapeHtml pattern.

---

## Task 1: Fix Dev Caddyfile (try_files + X-Frame-Options)

**Files:**

- Modify: `dev/Caddyfile:23` (X-Frame-Options)
- Modify: `dev/Caddyfile:61` (try_files)

**Step 1: Fix X-Frame-Options**

In `dev/Caddyfile` line 23, change `"DENY"` to `"SAMEORIGIN"`:

```text
X-Frame-Options "SAMEORIGIN"
```

**Why**: `DENY` prevents `index.html` from loading inside `shell.html`'s iframe even though they're same-origin. `SAMEORIGIN` allows same-origin framing while still blocking cross-origin attacks.

**Step 2: Fix try_files fallback**

In `dev/Caddyfile` line 61, change `/index.html` to `/shell.html`:

```text
try_files {path} /shell.html
```

**Why**: The shell+iframe architecture requires `shell.html` as the entry point. When Caddy can't find a file, it should fall back to `shell.html` (which hosts the iframe that loads `index.html`), not serve `index.html` directly outside an iframe.

**Step 3: Commit**

```bash
git add dev/Caddyfile
git commit -m "fix(dev): Caddyfile try_files to shell.html, X-Frame-Options SAMEORIGIN

The shell+iframe architecture requires shell.html as the entry point.
DENY blocked same-origin framing; SAMEORIGIN allows it safely."
```

---

### Task 2: Fix shellPlay() Intent Preservation (library.js)

**Files:**

- Modify: `library/web-v2/js/library.js:2555-2561` (shellPlay function)

**Step 1: Update shellPlay() to preserve play intent on redirect**

Replace the `shellPlay` function at lines 2555-2561 with:

```javascript
function shellPlay(book, resume) {
    if (inIframe) {
        whenShellReady(sp => sp.playBook(book, resume));
    } else {
        // Not in iframe — redirect to shell.html with play intent
        const bookId = book.bookId || book.id;
        sessionStorage.setItem('pendingPlay', JSON.stringify(book));
        sessionStorage.setItem('pendingPlayResume', resume ? '1' : '0');
        window.location.href = `shell.html?autoplay=${encodeURIComponent(bookId)}`;
    }
}
```

**Why**: When `index.html` is loaded outside the iframe (direct URL, bookmark, misconfigured proxy), the old code redirected to `shell.html` but lost the play intent. The book object goes to `sessionStorage` (survives the navigation), and the `autoplay` URL param signals shell.js to pick it up.

**Step 2: Commit**

```bash
git add library/web-v2/js/library.js
git commit -m "fix: shellPlay() preserves play intent on non-iframe redirect

When index.html is accessed outside the shell iframe, redirect to
shell.html with autoplay param + sessionStorage book data so playback
starts automatically after redirect instead of requiring a second click."
```

---

### Task 3: Add Autoplay Handling in shell.js

**Files:**

- Modify: `library/web-v2/js/shell.js:470-476` (DOMContentLoaded handler)

**Step 1: Add autoplay param check after shellPlayer creation**

Replace lines 470-476 with:

```javascript
// Initialize when DOM is ready.
// MUST use var (not let/const) so shellPlayer is a window property,
// accessible from the iframe via window.parent.shellPlayer.
var shellPlayer;
document.addEventListener('DOMContentLoaded', () => {
    shellPlayer = new ShellPlayer();

    // Check for autoplay intent (from non-iframe redirect)
    const params = new URLSearchParams(window.location.search);
    const autoplayId = params.get('autoplay');
    if (autoplayId) {
        const pending = sessionStorage.getItem('pendingPlay');
        const resume = sessionStorage.getItem('pendingPlayResume') === '1';
        if (pending) {
            sessionStorage.removeItem('pendingPlay');
            sessionStorage.removeItem('pendingPlayResume');
            // Small delay to let iframe load before showing player state
            setTimeout(() => shellPlayer.playBook(JSON.parse(pending), resume), 100);
        }
        // Clean URL — remove ?autoplay param
        history.replaceState(null, '', window.location.pathname);
    }
});
```

**Why**: When shell.html loads with `?autoplay=ID`, it retrieves the book from sessionStorage and starts playback automatically. The 100ms delay ensures the iframe has started loading so the player state message reaches it. `history.replaceState` cleans the URL so refreshing doesn't re-trigger autoplay.

**Step 2: Commit**

```bash
git add library/web-v2/js/shell.js
git commit -m "fix: shell.js handles autoplay param from non-iframe redirect

Reads ?autoplay=ID and pendingPlay from sessionStorage, starts playback
automatically, then cleans the URL. Completes the double-click fix."
```

---

### Task 4: Overlay Player Bar (No Layout Shift)

**Files:**

- Modify: `library/web-v2/css/shell.css:21-34` (iframe + player-active rules)
- Modify: `library/web-v2/js/shell.js` (playBook method, line 175-176)
- Modify: `library/web-v2/js/library.js:2581-2603` (message listener)

**Step 1: Remove iframe resize rule in shell.css**

Replace lines 21-34 with:

```css
/* Content iframe — full viewport, always full height.
   Player bar overlays on top (position: fixed, z-index: 9999).
   No height change = no layout shift when player appears. */
#content-frame {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    border: none;
}
```

This removes the `transition: height 0.3s ease` and the `body.player-active` rule entirely. The iframe is always 100% height. The player bar (already `position: fixed; z-index: 9999`) naturally overlays at the bottom.

**Step 2: Add playerVisible message in shell.js playBook()**

In `shell.js`, after line 176 (`document.body.classList.add('player-active');`), add:

```javascript
        // Notify iframe to add bottom padding (prevent content hiding behind player)
        this.sendToIframe({ type: 'playerVisible', visible: true });
```

And in the `close()` method (around line 231, after `document.body.classList.remove('player-active');`), add:

```javascript
        // Notify iframe to remove bottom padding
        this.sendToIframe({ type: 'playerVisible', visible: false });
```

**Step 3: Handle playerVisible message in library.js**

In the message listener at line 2582-2603, add a new case after the `playerClosed` block (line 2601), before the closing `});`:

```javascript
    } else if (data.type === 'playerVisible') {
        // Add/remove bottom padding so content isn't hidden behind overlay player bar
        // 100px covers both desktop (80px) and mobile (100px) player heights
        document.body.style.paddingBottom = data.visible ? '100px' : '0';
    }
```

**Step 4: Commit**

```bash
git add library/web-v2/css/shell.css library/web-v2/js/shell.js library/web-v2/js/library.js
git commit -m "fix: overlay player bar instead of resizing iframe

Remove iframe height transition that caused layout shift perceived as a
'refresh' on first play. Player bar overlays content instead. Iframe
receives playerVisible message to add bottom padding so nothing hides
behind the bar."
```

---

### Task 5: Add Landscape Media Query Width Constraints (responsive.css)

**Files:**

- Modify: `library/web-v2/css/responsive.css:233` (Section D)
- Modify: `library/web-v2/css/responsive.css:373` (Section D2)

**Step 1: Add max-width to Section D landscape query**

At line 233, change:

```css
@media (orientation: landscape) and (max-height: 500px) {
```

to:

```css
@media (orientation: landscape) and (max-height: 500px) and (max-width: 960px) {
```

**Step 2: Add max-width to Section D2 landscape query**

At line 373, change:

```css
@media (orientation: landscape) and (max-height: 700px) {
```

to:

```css
@media (orientation: landscape) and (max-height: 700px) and (max-width: 1024px) {
```

**Why**: Without width constraints, these queries match desktop browser windows that happen to have a short viewport height (e.g., a 1080p display with toolbars reducing content height below 700px). The `max-width` ensures only phone/small-tablet viewports in landscape mode get the compact treatment. Desktop windows are typically 1200px+ wide.

**Step 3: Commit**

```bash
git add library/web-v2/css/responsive.css
git commit -m "fix: add max-width to landscape media queries to prevent desktop match

Section D (max-height: 500px) now requires max-width: 960px.
Section D2 (max-height: 700px) now requires max-width: 1024px.
Prevents compact mobile layout from triggering on desktop windows
with short viewport heights."
```

---

### Task 6: Add Book Data Cache for Detail Modal (library.js)

**Files:**

- Modify: `library/web-v2/js/library.js:45` (constructor — add property)
- Modify: `library/web-v2/js/library.js:1363` (loadAudiobooks — cache books)

**Step 1: Add browseBooks property to constructor**

At line 45 (near `this.myLibraryBooks = [];`), add:

```javascript
        this.browseBooks = [];
```

**Step 2: Cache books after fetch**

At line 1363, change:

```javascript
            this.renderBooks(data.audiobooks);
```

to:

```javascript
            this.browseBooks = data.audiobooks;
            this.renderBooks(data.audiobooks);
```

**Why**: The detail modal needs access to the full book object (narrator, format, duration, etc.) when the user taps a compact card. Browse tab books weren't cached. My Library books already are (`this.myLibraryBooks`). This adds the same pattern.

**Step 3: Commit**

```bash
git add library/web-v2/js/library.js
git commit -m "feat: cache browse tab books for detail modal lookup

Stores fetched audiobooks in this.browseBooks so the mobile detail
modal can look up full book data by ID. Mirrors existing
this.myLibraryBooks pattern."
```

---

### Task 7: Add showBookDetail() Function (library.js)

**Files:**

- Modify: `library/web-v2/js/library.js` (add method after `showSupplements`, ~line 1537)

**Step 1: Add showBookDetail method to AudiobookLibraryV2 class**

Insert after `showSupplements()` (after line 1537). This method creates a bottom-sheet modal with full book details. All user-supplied text fields use `this.escapeHtml()` for XSS safety (same pattern as `createBookCard`). The modal is created via DOM elements with textContent for user strings and innerHTML only for structural markup with escaped values.

```javascript
    showBookDetail(bookId) {
        // Find book in cached data (browse or my-library tab)
        const book = this.browseBooks.find(b => b.id === bookId)
            || this.myLibraryBooks.find(b => (b.bookId || b.id) === bookId);
        if (!book) return;

        const savedPosition = getLocalPosition(book.id);
        const percentComplete = getLocalPercentComplete(book.id);
        const hasContinue = percentComplete > 0;
        const formatQuality = book.format ? book.format.toUpperCase() : 'M4B';
        const quality = book.quality ? ` ${book.quality}` : '';
        const hasSupplement = book.supplement_count > 0;

        // Remove existing detail modal if any
        document.getElementById('book-detail-modal')?.remove();

        // Build modal via DOM API for XSS safety on user-supplied fields
        const modal = document.createElement('div');
        modal.id = 'book-detail-modal';
        modal.className = 'modal book-detail-sheet show';

        const content = document.createElement('div');
        content.className = 'modal-content book-detail-content';

        // Header
        const header = document.createElement('div');
        header.className = 'modal-header';
        const h2 = document.createElement('h2');
        h2.textContent = 'Book Details';
        const closeBtn = document.createElement('button');
        closeBtn.className = 'modal-close';
        closeBtn.title = 'Close dialog';
        closeBtn.textContent = '\u00D7';
        header.appendChild(h2);
        header.appendChild(closeBtn);

        // Body
        const body = document.createElement('div');
        body.className = 'modal-body book-detail-body';

        // Cover
        const coverDiv = document.createElement('div');
        coverDiv.className = 'detail-cover';
        if (book.cover_path) {
            const img = document.createElement('img');
            img.src = '/covers/' + book.cover_path;
            img.alt = book.title || '';
            coverDiv.appendChild(img);
        } else {
            const placeholder = document.createElement('span');
            placeholder.className = 'book-cover-placeholder';
            placeholder.style.fontSize = '3rem';
            placeholder.textContent = '\u{1F4D6}';
            coverDiv.appendChild(placeholder);
        }

        // Info section
        const info = document.createElement('div');
        info.className = 'detail-info';

        const titleEl = document.createElement('div');
        titleEl.className = 'detail-title';
        titleEl.textContent = book.title || 'Unknown Title';
        info.appendChild(titleEl);

        if (book.author) {
            const authorEl = document.createElement('div');
            authorEl.className = 'detail-author';
            authorEl.textContent = 'by ' + book.author;
            info.appendChild(authorEl);
        }

        if (book.narrator) {
            const narratorEl = document.createElement('div');
            narratorEl.className = 'detail-narrator';
            narratorEl.textContent = 'Narrated by ' + book.narrator;
            info.appendChild(narratorEl);
        }

        const meta = document.createElement('div');
        meta.className = 'detail-meta';
        const fmtSpan = document.createElement('span');
        fmtSpan.textContent = formatQuality + quality;
        const durSpan = document.createElement('span');
        durSpan.textContent = book.duration_formatted || (Math.round(book.duration_hours || 0) + 'h');
        meta.appendChild(fmtSpan);
        meta.appendChild(durSpan);
        info.appendChild(meta);

        if (hasContinue) {
            const progressDiv = document.createElement('div');
            progressDiv.className = 'detail-progress';
            const barBg = document.createElement('div');
            barBg.className = 'progress-bar-bg';
            const barFill = document.createElement('div');
            barFill.className = 'progress-bar-fill';
            barFill.style.width = percentComplete + '%';
            barBg.appendChild(barFill);
            const pctText = document.createElement('span');
            pctText.className = 'progress-text';
            pctText.textContent = percentComplete + '% complete';
            progressDiv.appendChild(barBg);
            progressDiv.appendChild(pctText);
            info.appendChild(progressDiv);
        }

        if (hasSupplement) {
            const badge = document.createElement('div');
            badge.className = 'detail-badge';
            badge.textContent = 'PDF Supplement Available';
            info.appendChild(badge);
        }

        // Actions
        const actions = document.createElement('div');
        actions.className = 'detail-actions';

        const playBtn = document.createElement('button');
        playBtn.className = 'btn-play';
        playBtn.textContent = '\u25B6 Play';
        playBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            shellPlay(book, false);
            modal.remove();
        });

        const resumeBtn = document.createElement('button');
        resumeBtn.className = 'btn-resume';
        resumeBtn.textContent = '\u23EF Resume';
        resumeBtn.disabled = !hasContinue;
        if (hasContinue && savedPosition) {
            resumeBtn.title = 'Resume from ' + formatPlaybackTime(savedPosition.position);
        }
        resumeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            shellPlay(book, true);
            modal.remove();
        });

        const downloadBtn = document.createElement('button');
        downloadBtn.className = 'btn-download download-button';
        downloadBtn.style.display = 'none';
        downloadBtn.textContent = '\u2B07 Download';
        downloadBtn.title = 'Download this audiobook';
        downloadBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            library.downloadAudiobook(book.id);
        });

        actions.appendChild(playBtn);
        actions.appendChild(resumeBtn);
        actions.appendChild(downloadBtn);

        body.appendChild(coverDiv);
        body.appendChild(info);
        body.appendChild(actions);

        content.appendChild(header);
        content.appendChild(body);
        modal.appendChild(content);

        // Close on backdrop click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.remove();
        });
        closeBtn.addEventListener('click', () => modal.remove());

        document.body.appendChild(modal);

        // Update download button visibility for current user permissions
        this.updateDownloadButtons();
    }
```

**Step 2: Commit**

```bash
git add library/web-v2/js/library.js
git commit -m "feat: add showBookDetail() method for mobile info modal

Creates a bottom-sheet modal with full book details (cover, title,
author, narrator, format, duration, progress, actions) when tapping
a compact mobile card. Uses DOM API with textContent for all
user-supplied strings (XSS safe). Closes on backdrop tap or X button."
```

---

### Task 8: Wire Card Tap Handler for Compact Viewports (library.js)

**Files:**

- Modify: `library/web-v2/js/library.js` (constructor, after browseBooks init)

**Step 1: Add setupCompactCardTap() call in constructor**

In the constructor (after the new `this.browseBooks = [];` line from Task 6), add:

```javascript
        // Compact viewport: card tap opens detail modal
        this.setupCompactCardTap();
```

**Step 2: Add setupCompactCardTap method to class**

Insert after `showBookDetail()`:

```javascript
    setupCompactCardTap() {
        const grid = document.getElementById('books-grid');
        if (!grid) return;

        grid.addEventListener('click', (e) => {
            // Only active at compact viewports — desktop cards show all info inline
            const isCompact = window.matchMedia(
                '(max-width: 480px), ' +
                '(orientation: landscape) and (max-height: 500px) and (max-width: 960px), ' +
                '(orientation: landscape) and (max-height: 700px) and (max-width: 1024px)'
            ).matches;
            if (!isCompact) return;

            const card = e.target.closest('.book-card');
            if (!card) return;

            const bookId = parseInt(card.dataset.id, 10);
            if (bookId) this.showBookDetail(bookId);
        });
    }
```

**Why**: Uses event delegation on the grid container — attached once in the constructor, works across all page re-renders. The `matchMedia` check mirrors the CSS media queries so the tap handler is active exactly when the compact layout is. Play/Resume buttons still work directly because they call `event.stopPropagation()`.

**Step 3: Commit**

```bash
git add library/web-v2/js/library.js
git commit -m "feat: wire compact card tap to detail modal via event delegation

On compact viewports, tapping a book card opens the detail modal.
Play/Resume buttons still work directly via stopPropagation. Uses
event delegation on the grid container, attached once in constructor."
```

---

### Task 9: Add Bottom-Sheet Modal CSS (modals.css)

**Files:**

- Modify: `library/web-v2/css/modals.css` (insert before the responsive `@media` block at line 382)

**Step 1: Add bottom-sheet modal styles**

Insert before the `@media (max-width: 768px)` block at line 382:

```css
/* ============================================
   Book Detail Bottom Sheet (mobile compact view)
   ============================================ */
.book-detail-sheet {
    align-items: flex-end;
    padding: 0;
}

.book-detail-content {
    width: 100%;
    max-width: 500px;
    max-height: 85vh;
    border-radius: 12px 12px 0 0;
    border-bottom: none;
    animation: slideUp 0.25s ease-out;
}

@keyframes slideUp {
    from { transform: translateY(100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
}

.book-detail-body {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1rem;
    padding: 1.5rem;
}

.detail-cover {
    width: 120px;
    height: 120px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--deco-charcoal);
    border: 2px solid var(--gold);
}

.detail-cover img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.detail-info {
    text-align: center;
    width: 100%;
}

.detail-title {
    font-size: 1.1rem;
    font-weight: bold;
    color: var(--gold-bright);
    margin-bottom: 0.25rem;
}

.detail-author {
    font-size: 0.95rem;
    color: var(--cream);
    margin-bottom: 0.15rem;
}

.detail-narrator {
    font-size: 0.85rem;
    color: var(--cream-light);
    font-style: italic;
    margin-bottom: 0.5rem;
}

.detail-meta {
    display: flex;
    justify-content: center;
    gap: 1rem;
    font-size: 0.85rem;
    color: var(--cream-light);
    margin-bottom: 0.5rem;
}

.detail-progress {
    margin: 0.5rem 0;
    width: 100%;
}

.detail-badge {
    display: inline-block;
    padding: 0.25rem 0.75rem;
    background: var(--emerald);
    color: var(--cream-light);
    font-size: 0.75rem;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-top: 0.5rem;
}

.detail-actions {
    display: flex;
    gap: 0.5rem;
    width: 100%;
    justify-content: center;
    flex-wrap: wrap;
}

.detail-actions .btn-play,
.detail-actions .btn-resume,
.detail-actions .btn-download {
    flex: 1;
    min-width: 100px;
    padding: 0.75rem 1rem;
    font-size: 1rem;
}
```

**Step 2: Commit**

```bash
git add library/web-v2/css/modals.css
git commit -m "feat: add bottom-sheet modal CSS for mobile book detail view

Art Deco styled bottom sheet slides up with cover, full metadata,
progress, and full-size action buttons. Consistent with existing
modal design system. Animates with slideUp keyframe."
```

---

### Task 10: Update CSS Version Cache-Busters

**Files:**

- Modify: `library/web-v2/shell.html:7` (shell.css version)
- Modify: `library/web-v2/shell.html:42` (shell.js version)
- Check: `library/web-v2/css/library.css` for `@import` version params on `responsive.css` and `modals.css`

**Step 1: Bump cache-buster versions**

In `shell.html`, update the version query params on shell.css and shell.js from `?v=6.7.1.5` to the next version (match VERSION file if bumped, otherwise use `?v=6.7.1.6` or similar).

Also update `@import` version params in `library.css` for `responsive.css` and `modals.css`.

**Step 2: Commit**

```bash
git add library/web-v2/shell.html library/web-v2/css/library.css
git commit -m "chore: bump CSS/JS cache-buster versions for UI/UX fixes"
```

---

### Task 11: Manual Verification

**Desktop browser (any of the three monitors):**

1. Open `https://localhost:9443/` (dev Caddy) — should load `shell.html` with iframe
2. Verify books show full Art Deco cards (not compact icons) on desktop
3. Click Play on any book — should play immediately on first click, no layout shift
4. Player bar should appear at bottom overlaying content, not pushing it up
5. Scroll to bottom of book list — last row should have padding so it's not hidden behind player
6. Close player — bottom padding should disappear

**Mobile simulation (DevTools responsive mode, 375x667):**

1. Verify compact grid with small icons
2. Tap a book card (not the Play button) — detail bottom-sheet should slide up
3. Detail sheet should show: cover, title, author, narrator, format, duration, progress, actions
4. Tap Play in the detail sheet — should play and close the modal
5. Tap the Play button directly on a compact card (not the card body) — should play without opening modal

**Direct index.html access:**

1. Navigate directly to `https://localhost:9443/index.html`
2. Click Play — should redirect to `shell.html?autoplay=ID` and start playing automatically

**Landscape phone simulation (DevTools, 667x375 landscape):**

1. Verify compact grid appears only at narrow widths (under 960px)
2. At desktop width (over 1024px), landscape with short height should NOT trigger compact

---

## Summary of All Changes

| File | Task | What Changes |
|------|------|-------------|
| `dev/Caddyfile` | 1 | try_files to /shell.html, X-Frame-Options to SAMEORIGIN |
| `library/web-v2/js/library.js` | 2,6,7,8 | shellPlay intent, browseBooks cache, showBookDetail(), setupCompactCardTap() |
| `library/web-v2/js/shell.js` | 3,4 | autoplay param handling, playerVisible messages |
| `library/web-v2/css/shell.css` | 4 | Remove iframe resize, keep overlay |
| `library/web-v2/css/responsive.css` | 5 | Add max-width to landscape queries |
| `library/web-v2/css/modals.css` | 9 | Bottom-sheet modal styles |
| `library/web-v2/shell.html` | 10 | Cache-buster version bumps |
