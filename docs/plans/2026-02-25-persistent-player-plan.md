# Persistent Player Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make audio playback persist across page navigation, fix empty My Library, and remove dead Audible sync code.

**Architecture:** A new `shell.html` wraps all post-auth pages in an iframe while keeping the `<audio>` element and player UI in the shell (parent frame). Content pages communicate with the shell via `postMessage`. The My Library bug is a missing `credentials: 'include'` on two fetch calls. Dead Audible frontend code gets deleted.

**Tech Stack:** Vanilla JS, HTML, CSS, Python/Flask (security headers only). No frameworks.

**Design doc:** `docs/plans/2026-02-25-persistent-player-design.md`

---

## Task 1: Dead Audible Sync Removal

Remove dead Audible sync code from `library.js`. Backend was already cleaned up. This is a standalone cleanup with no dependencies.

**Files:**

- Modify: `library/web-v2/js/library.js`
- Create: `library/tests/test_audible_sync_cleanup.py`

**Step 1: Write the failing test**

Create `library/tests/test_audible_sync_cleanup.py`:

```python
"""Verify all Audible sync frontend code has been removed."""

from pathlib import Path

LIBRARY_JS = Path(__file__).parent.parent / "web-v2" / "js" / "library.js"


class TestAudibleSyncRemoval:
    """Audible sync was removed from the backend. Frontend remnants must go too."""

    def test_no_audible_sync_method(self):
        content = LIBRARY_JS.read_text()
        assert "syncWithAudible" not in content, (
            "syncWithAudible method should be removed from library.js"
        )

    def test_no_audible_sync_timer(self):
        content = LIBRARY_JS.read_text()
        assert "audibleSyncInterval" not in content
        assert "audibleSyncDelayMs" not in content
        assert "startAudibleSyncTimer" not in content
        assert "stopAudibleSyncTimer" not in content

    def test_no_audible_sync_references(self):
        """No references to 'Audible sync' should remain in library.js."""
        content = LIBRARY_JS.read_text()
        assert "Audible sync" not in content
        assert "Audible service" not in content
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && venv/bin/pytest library/tests/test_audible_sync_cleanup.py -v`
Expected: FAIL — all 3 tests fail because Audible code is still present.

**Step 3: Remove dead Audible code from library.js**

In `library/web-v2/js/library.js`, make these removals:

1. **Lines 2036-2037**: Remove `audibleSyncInterval` and `audibleSyncDelayMs` properties from `AudioPlayer` constructor.

2. **Lines 2300-2301**: Remove `this.startAudibleSyncTimer();` call and its comment from `playAudiobook()`.

3. **Lines 2315-2347**: Remove entire `startAudibleSyncTimer()` and `stopAudibleSyncTimer()` methods (including JSDoc comments).

4. **Lines 2418-2419**: Remove `this.stopAudibleSyncTimer();` call and its comment from `close()`.

5. **Lines 2430-2433**: Remove the Audible sync block from `close()`:

   ```js
   // Final Audible sync on close
   if (this.currentBook.asin) {
       playbackManager.syncWithAudible(this.currentBook.id);
   }
   ```

6. **Lines 2985**: Change the comment `// Both have data - use furthest ahead (same logic as Audible sync)` to `// Both have data - use furthest ahead`.

7. **Lines 3004-3037**: Remove entire `syncWithAudible()` method from `PlaybackManager` class (including JSDoc).

**Step 4: Run test to verify it passes**

Run: `venv/bin/pytest library/tests/test_audible_sync_cleanup.py -v`
Expected: PASS — all 3 tests pass.

**Step 5: Run full test suite for regressions**

Run: `venv/bin/pytest library/tests/ -x -q --timeout=60 2>&1 | tail -5`
Expected: All previously-passing tests still pass.

**Step 6: Commit**

```bash
git add library/web-v2/js/library.js library/tests/test_audible_sync_cleanup.py
git commit -m "refactor: remove dead Audible sync code from frontend

Backend Audible sync was removed previously but ~30 lines of frontend
code remained, calling a non-existent endpoint. Remove all references:
startAudibleSyncTimer, stopAudibleSyncTimer, syncWithAudible, and
related properties."
```

---

## Task 2: My Library Credentials Fix

Add `credentials: 'include'` to `savePositionToAPI()` and `getPositionFromAPI()` in `PlaybackManager`. Without this, session cookies aren't sent, so the server sees unauthenticated requests, never creates per-user listening history, and My Library stays empty.

**Files:**

- Modify: `library/web-v2/js/library.js`
- Create: `library/tests/test_playback_credentials.py`

**Step 1: Write the failing test**

Create `library/tests/test_playback_credentials.py`:

```python
"""Verify PlaybackManager fetch calls include credentials for auth cookie."""

import re
from pathlib import Path

LIBRARY_JS = Path(__file__).parent.parent / "web-v2" / "js" / "library.js"


def _extract_fetch_block(content: str, method_name: str) -> str:
    """Extract the body of a method from library.js by name."""
    pattern = rf"(async\s+)?{method_name}\s*\([^)]*\)\s*\{{"
    match = re.search(pattern, content)
    if not match:
        raise ValueError(f"Method {method_name} not found in library.js")
    start = match.start()
    # Find matching closing brace by counting
    depth = 0
    for i, ch in enumerate(content[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    raise ValueError(f"Could not find end of method {method_name}")


class TestPlaybackManagerCredentials:
    """Session cookie must be sent with all position API calls."""

    def test_save_position_has_credentials(self):
        content = LIBRARY_JS.read_text()
        block = _extract_fetch_block(content, "savePositionToAPI")
        assert "credentials" in block, (
            "savePositionToAPI must include credentials: 'include' "
            "in its fetch options so the session cookie is sent"
        )

    def test_get_position_has_credentials(self):
        content = LIBRARY_JS.read_text()
        block = _extract_fetch_block(content, "getPositionFromAPI")
        assert "credentials" in block, (
            "getPositionFromAPI must include credentials: 'include' "
            "in its fetch options so the session cookie is sent"
        )

    def test_flush_to_api_calls_save_position(self):
        """flushToAPI delegates to savePositionToAPI, so it inherits credentials."""
        content = LIBRARY_JS.read_text()
        block = _extract_fetch_block(content, "flushToAPI")
        assert "savePositionToAPI" in block

    def test_load_my_library_has_credentials(self):
        """loadMyLibrary should already have credentials (sanity check)."""
        content = LIBRARY_JS.read_text()
        block = _extract_fetch_block(content, "loadMyLibrary")
        assert "credentials" in block
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/pytest library/tests/test_playback_credentials.py -v`
Expected: `test_save_position_has_credentials` and `test_get_position_has_credentials` FAIL. The other two pass.

**Step 3: Add credentials to both fetch calls**

In `library/web-v2/js/library.js`:

1. **`savePositionToAPI()`** — add `credentials: 'include'` to the fetch options:

   Change:

   ```js
   const response = await fetch(`${API_BASE}/position/${fileId}`, {
       method: 'PUT',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify({ position_ms: positionMs })
   });
   ```

   To:

   ```js
   const response = await fetch(`${API_BASE}/position/${fileId}`, {
       method: 'PUT',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify({ position_ms: positionMs }),
       credentials: 'include'
   });
   ```

2. **`getPositionFromAPI()`** — add `credentials: 'include'`:

   Change:

   ```js
   const response = await fetch(`${API_BASE}/position/${fileId}`);
   ```

   To:

   ```js
   const response = await fetch(`${API_BASE}/position/${fileId}`, {
       credentials: 'include'
   });
   ```

**Step 4: Run test to verify it passes**

Run: `venv/bin/pytest library/tests/test_playback_credentials.py -v`
Expected: PASS — all 4 tests pass.

**Step 5: Run full test suite**

Run: `venv/bin/pytest library/tests/ -x -q --timeout=60 2>&1 | tail -5`
Expected: All pass.

**Step 6: Commit**

```bash
git add library/web-v2/js/library.js library/tests/test_playback_credentials.py
git commit -m "fix: add credentials to position API fetch calls

savePositionToAPI() and getPositionFromAPI() were missing
credentials: 'include', so session cookies were not sent. The server
saw unauthenticated requests, never created per-user listening history,
and My Library appeared empty for all users."
```

---

## Task 3: Security Header Changes

Change `X-Frame-Options` from `DENY` to `SAMEORIGIN` and CSP `frame-ancestors` from `'none'` to `'self'` to allow same-origin iframe embedding. Add `frame-src 'self'` to permit the iframe element.

**Files:**

- Modify: `library/backend/api_modular/core.py:45-58`
- Create: `library/tests/test_security_headers_iframe.py`

**Step 1: Write the failing test**

Create `library/tests/test_security_headers_iframe.py`:

```python
"""Verify security headers allow same-origin iframe embedding."""

import re
from pathlib import Path

CORE_PY = (
    Path(__file__).parent.parent / "backend" / "api_modular" / "core.py"
)


class TestSecurityHeadersForIframe:
    """Headers must allow same-origin framing for shell.html iframe architecture."""

    def test_x_frame_options_sameorigin(self):
        content = CORE_PY.read_text()
        assert '"SAMEORIGIN"' in content, (
            "X-Frame-Options must be SAMEORIGIN (not DENY) for iframe shell"
        )
        assert '"DENY"' not in content or "X-Frame-Options" not in content.split('"DENY"')[0].split("\n")[-1], (
            "X-Frame-Options must not be DENY"
        )

    def test_csp_frame_ancestors_self(self):
        content = CORE_PY.read_text()
        assert "frame-ancestors 'self'" in content, (
            "CSP frame-ancestors must be 'self' (not 'none')"
        )
        assert "frame-ancestors 'none'" not in content, (
            "CSP frame-ancestors must not be 'none'"
        )

    def test_csp_frame_src_self(self):
        content = CORE_PY.read_text()
        assert "frame-src 'self'" in content, (
            "CSP must include frame-src 'self' to permit iframe element"
        )

    def test_other_security_headers_unchanged(self):
        """Verify we didn't accidentally remove other security headers."""
        content = CORE_PY.read_text()
        assert "X-Content-Type-Options" in content
        assert "nosniff" in content
        assert "Referrer-Policy" in content
        assert "Permissions-Policy" in content
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/pytest library/tests/test_security_headers_iframe.py -v`
Expected: First 3 tests FAIL (DENY still present, frame-ancestors 'none' still present, frame-src missing).

**Step 3: Update security headers in core.py**

In `library/backend/api_modular/core.py`, modify `add_security_headers()`:

1. **Line 48**: Change `"DENY"` to `"SAMEORIGIN"`:

   ```python
   response.headers["X-Frame-Options"] = "SAMEORIGIN"
   ```

2. **Line 57**: Change `"frame-ancestors 'none'"` to `"frame-ancestors 'self'; frame-src 'self'"`:

   ```python
   "frame-ancestors 'self'; "
   "frame-src 'self'"
   ```

The full CSP string becomes:

```python
response.headers["Content-Security-Policy"] = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'self'; "
    "frame-src 'self'"
)
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/pytest library/tests/test_security_headers_iframe.py -v`
Expected: PASS — all 4 tests pass.

**Step 5: Run full test suite**

Run: `venv/bin/pytest library/tests/ -x -q --timeout=60 2>&1 | tail -5`
Expected: All pass.

**Step 6: Commit**

```bash
git add library/backend/api_modular/core.py library/tests/test_security_headers_iframe.py
git commit -m "feat: allow same-origin iframe embedding for persistent player

Change X-Frame-Options: DENY → SAMEORIGIN and CSP frame-ancestors:
'none' → 'self'. Add frame-src 'self'. Clickjacking protection is
maintained (cross-origin framing still blocked)."
```

---

## Task 4: Shell Page — HTML Structure

Create `shell.html` — the outer page that holds the iframe and player bar. This is the container that keeps `<audio>` alive across navigation.

**Files:**

- Create: `library/web-v2/shell.html`
- Create: `library/tests/test_shell_page.py`

**Step 1: Write the failing test**

Create `library/tests/test_shell_page.py`:

```python
"""Verify shell.html structure for persistent player architecture."""

from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web-v2"
SHELL_HTML = WEB_DIR / "shell.html"


class TestShellPageExists:

    def test_shell_html_exists(self):
        assert SHELL_HTML.exists(), "shell.html must exist in web-v2/"

    def test_has_iframe(self):
        content = SHELL_HTML.read_text()
        assert 'id="content-frame"' in content, (
            "shell.html must have an iframe with id='content-frame'"
        )

    def test_iframe_default_src(self):
        content = SHELL_HTML.read_text()
        assert 'src="index.html"' in content, (
            "iframe default src must be index.html"
        )

    def test_has_audio_element(self):
        content = SHELL_HTML.read_text()
        assert 'id="audio-element"' in content, (
            "shell.html must contain the <audio> element"
        )

    def test_has_player_bar(self):
        content = SHELL_HTML.read_text()
        assert 'id="shell-player"' in content, (
            "shell.html must have a player bar with id='shell-player'"
        )

    def test_player_bar_hidden_by_default(self):
        content = SHELL_HTML.read_text()
        assert 'hidden' in content.split('id="shell-player"')[1][:100], (
            "Player bar must be hidden by default"
        )

    def test_has_shell_js(self):
        content = SHELL_HTML.read_text()
        assert 'js/shell.js' in content, "shell.html must load shell.js"

    def test_has_shell_css(self):
        content = SHELL_HTML.read_text()
        assert 'css/shell.css' in content, "shell.html must load shell.css"

    def test_no_library_js(self):
        """shell.html should NOT load library.js — that's for content pages."""
        content = SHELL_HTML.read_text()
        assert 'library.js' not in content, (
            "shell.html must not load library.js (that belongs in iframe content)"
        )
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/pytest library/tests/test_shell_page.py -v`
Expected: FAIL — `test_shell_html_exists` fails, rest error.

**Step 3: Create shell.html**

Create `library/web-v2/shell.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>The Library - Audiobook Collection</title>
    <link rel="stylesheet" href="css/shell.css">
</head>
<body>
    <!-- Content iframe: loads all authenticated pages -->
    <iframe id="content-frame" src="index.html" title="Library content"></iframe>

    <!-- Persistent player bar (hidden until a book is played) -->
    <div id="shell-player" hidden>
        <div class="player-cover-wrap">
            <img id="sp-cover" src="" alt="" class="sp-cover">
        </div>
        <div class="player-info-bar">
            <div class="sp-title" id="sp-title">-</div>
            <div class="sp-author" id="sp-author">-</div>
        </div>
        <div class="player-controls-bar">
            <button class="sp-btn" id="sp-rewind" title="Back 30 seconds">-30s</button>
            <button class="sp-btn sp-play" id="sp-play-pause" title="Play / Pause">&#9654;</button>
            <button class="sp-btn" id="sp-forward" title="Forward 30 seconds">+30s</button>
        </div>
        <div class="player-progress-bar">
            <span id="sp-current-time">0:00</span>
            <input type="range" id="sp-progress" class="sp-progress" min="0" max="1000" value="0">
            <span id="sp-total-time">0:00</span>
        </div>
        <div class="player-extras-bar">
            <input type="range" id="sp-volume" class="sp-volume" min="0" max="100" value="100" title="Volume">
            <span id="sp-speed-display">1x</span>
            <button class="sp-btn sp-btn-sm" id="sp-speed" title="Cycle playback speed">Speed</button>
            <button class="sp-btn sp-btn-sm sp-close" id="sp-close" title="Close player">&times;</button>
        </div>
        <audio id="audio-element"></audio>
    </div>

    <script src="js/session-persistence.js"></script>
    <script src="js/shell.js"></script>
</body>
</html>
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/pytest library/tests/test_shell_page.py -v`
Expected: PASS — all 9 tests pass.

**Step 5: Commit**

```bash
git add library/web-v2/shell.html library/tests/test_shell_page.py
git commit -m "feat: add shell.html with iframe and persistent player bar

The shell page wraps all authenticated content in an iframe. The audio
element and player controls live in the shell so they survive page
navigation. Player bar is hidden until a book is played."
```

---

## Task 5: Shell CSS

Create `shell.css` — layout styles for the shell page (iframe sizing, player bar at bottom).

**Files:**

- Create: `library/web-v2/css/shell.css`
- Create: `library/tests/test_shell_css.py`

**Step 1: Write the failing test**

Create `library/tests/test_shell_css.py`:

```python
"""Verify shell.css exists and contains required layout rules."""

from pathlib import Path

SHELL_CSS = Path(__file__).parent.parent / "web-v2" / "css" / "shell.css"


class TestShellCSS:

    def test_shell_css_exists(self):
        assert SHELL_CSS.exists(), "shell.css must exist in web-v2/css/"

    def test_iframe_fills_viewport(self):
        content = SHELL_CSS.read_text()
        assert "#content-frame" in content

    def test_player_bar_fixed_bottom(self):
        content = SHELL_CSS.read_text()
        assert "#shell-player" in content
        assert "fixed" in content or "sticky" in content

    def test_responsive_mobile(self):
        """Shell CSS should handle mobile viewports."""
        content = SHELL_CSS.read_text()
        assert "@media" in content
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/pytest library/tests/test_shell_css.py -v`
Expected: FAIL — file doesn't exist.

**Step 3: Create shell.css**

Create `library/web-v2/css/shell.css`. Match the existing dark theme from `library.css` (`--deep-burgundy: #1a0a0a`, `--gold: #c9a84c`, `--parchment: #f0e6d3`):

```css
/* Shell layout: iframe content + persistent player bar */

:root {
    --deep-burgundy: #1a0a0a;
    --gold: #c9a84c;
    --parchment: #f0e6d3;
    --leather: #3a1c1c;
    --dark-wood: #2a1515;
    --player-height: 80px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

html, body {
    height: 100%;
    overflow: hidden;
    background: var(--deep-burgundy);
    font-family: 'Georgia', 'Times New Roman', serif;
}

/* Content iframe — full viewport, shrinks when player visible */
#content-frame {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    border: none;
    transition: height 0.3s ease;
}

body.player-active #content-frame {
    height: calc(100% - var(--player-height));
}

/* Player bar — fixed at bottom, hidden by default via hidden attr */
#shell-player {
    position: fixed;
    bottom: 0;
    left: 0;
    width: 100%;
    height: var(--player-height);
    background: linear-gradient(to top, var(--deep-burgundy), var(--dark-wood));
    border-top: 1px solid var(--gold);
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 16px;
    color: var(--parchment);
    z-index: 9999;
}

/* Cover art thumbnail */
.sp-cover {
    width: 56px;
    height: 56px;
    border-radius: 4px;
    object-fit: cover;
    background: var(--leather);
}

.player-cover-wrap { flex-shrink: 0; }

/* Title / Author */
.player-info-bar {
    flex: 1;
    min-width: 0;
    overflow: hidden;
}

.sp-title {
    font-size: 0.95rem;
    font-weight: bold;
    color: var(--gold);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.sp-author {
    font-size: 0.8rem;
    color: var(--parchment);
    opacity: 0.8;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* Controls */
.player-controls-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
}

.sp-btn {
    background: none;
    border: 1px solid var(--gold);
    color: var(--gold);
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
    font-family: inherit;
}

.sp-btn:hover { background: rgba(201, 168, 76, 0.15); }
.sp-play { font-size: 1.2rem; padding: 4px 14px; }
.sp-btn-sm { padding: 2px 8px; font-size: 0.75rem; }
.sp-close { border-color: #888; color: #888; }
.sp-close:hover { border-color: #f44; color: #f44; }

/* Progress */
.player-progress-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    font-size: 0.75rem;
    color: var(--parchment);
    opacity: 0.9;
}

.sp-progress {
    width: 120px;
    accent-color: var(--gold);
}

/* Volume & speed */
.player-extras-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    font-size: 0.75rem;
    color: var(--parchment);
}

.sp-volume {
    width: 70px;
    accent-color: var(--gold);
}

#sp-speed-display { min-width: 2em; text-align: center; }

/* Mobile: stack vertically */
@media (max-width: 768px) {
    :root { --player-height: 120px; }

    #shell-player {
        flex-wrap: wrap;
        padding: 8px 12px;
        gap: 6px;
    }

    .player-info-bar { flex-basis: calc(100% - 70px); }
    .player-progress-bar { flex-basis: 100%; order: 10; }
    .sp-progress { flex: 1; }
}
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/pytest library/tests/test_shell_css.py -v`
Expected: PASS — all 4 tests pass.

**Step 5: Commit**

```bash
git add library/web-v2/css/shell.css library/tests/test_shell_css.py
git commit -m "feat: add shell.css with player bar and iframe layout

Dark theme matching existing library.css. Player bar fixed at bottom
with cover art, title, controls, progress, volume, and speed.
Responsive layout stacks on mobile."
```

---

## Task 6: Shell JavaScript — Player Logic & postMessage

Create `shell.js` — the shell-side JavaScript that manages the `<audio>` element, player controls, and postMessage communication with iframe content.

**Files:**

- Create: `library/web-v2/js/shell.js`
- Create: `library/tests/test_shell_js.py`

**Step 1: Write the failing test**

Create `library/tests/test_shell_js.py`:

```python
"""Verify shell.js contains required player and messaging logic."""

from pathlib import Path

SHELL_JS = Path(__file__).parent.parent / "web-v2" / "js" / "shell.js"


class TestShellJS:

    def test_shell_js_exists(self):
        assert SHELL_JS.exists(), "shell.js must exist in web-v2/js/"

    def test_has_message_listener(self):
        content = SHELL_JS.read_text()
        assert "addEventListener" in content
        assert "'message'" in content or '"message"' in content

    def test_handles_play_message(self):
        content = SHELL_JS.read_text()
        assert "'play'" in content or '"play"' in content

    def test_handles_pause_message(self):
        content = SHELL_JS.read_text()
        assert "'pause'" in content or '"pause"' in content

    def test_handles_seek_message(self):
        content = SHELL_JS.read_text()
        assert "'seek'" in content or '"seek"' in content

    def test_origin_validation(self):
        """Messages must validate origin to prevent cross-origin attacks."""
        content = SHELL_JS.read_text()
        assert "origin" in content, (
            "shell.js must validate message origin"
        )

    def test_sends_player_state(self):
        content = SHELL_JS.read_text()
        assert "playerState" in content, (
            "shell.js must send playerState messages to iframe"
        )

    def test_has_credentials_on_api_calls(self):
        """Any fetch calls in shell.js must include credentials."""
        content = SHELL_JS.read_text()
        if "fetch(" in content:
            # Count fetch calls and credentials includes
            import re
            fetches = len(re.findall(r'fetch\(', content))
            creds = len(re.findall(r"credentials\s*:\s*['\"]include['\"]", content))
            assert creds >= fetches, (
                f"Found {fetches} fetch calls but only {creds} with credentials: 'include'"
            )

    def test_has_media_session(self):
        """Shell should set up Media Session API for lockscreen controls."""
        content = SHELL_JS.read_text()
        assert "mediaSession" in content

    def test_saves_position_with_credentials(self):
        """Position saves must include credentials."""
        content = SHELL_JS.read_text()
        assert "credentials" in content
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/pytest library/tests/test_shell_js.py -v`
Expected: FAIL — file doesn't exist.

**Step 3: Create shell.js**

Create `library/web-v2/js/shell.js`. This file contains the `ShellPlayer` class that manages the `<audio>` element, all player controls, position persistence (both localStorage and API), and postMessage communication with the iframe.

The `ShellPlayer` class must:

- Initialize audio element and all control listeners (play/pause, rewind, forward, volume, speed, progress bar, close)
- Listen for `message` events from the iframe (play, pause, resume, seek, getPlayerState)
- Validate message origin (`event.origin === window.location.origin`)
- Send `playerState` messages back to the iframe on every timeupdate
- Send `playerClosed` message when player is closed
- Save position to localStorage (immediate) and API (debounced every 15s) with `credentials: 'include'`
- Load best position from localStorage + API on play (furthest ahead wins)
- Set up Media Session API for lockscreen/notification controls
- Show/hide the player bar (toggle `player-active` class on `<body>`)
- Support playback speed cycling (0.5x through 2.5x)

Key implementation points (carry over from existing `AudioPlayer` + `PlaybackManager` classes in library.js):

- API base URL: Use relative `/api` (same as content pages)
- Position save debounce: 15 seconds (`apiSaveDelay`)
- Position storage key format: `audiobook_position_${fileId}`
- Speed storage key: `audiobook_speed`
- Audio format detection: Check for Ogg/Opus support, fall back to `?format=webm` for Safari
- CORS mode: `audio.crossOrigin = 'anonymous'`
- Position filtering: Skip positions >95% or <30s in `getPosition()`

**Step 4: Run test to verify it passes**

Run: `venv/bin/pytest library/tests/test_shell_js.py -v`
Expected: PASS — all 10 tests pass.

**Step 5: Commit**

```bash
git add library/web-v2/js/shell.js library/tests/test_shell_js.py
git commit -m "feat: add shell.js with player logic and postMessage bridge

ShellPlayer class manages the persistent audio element, controls,
position tracking (localStorage + API with credentials), and
bidirectional postMessage communication with iframe content pages.
Includes Media Session API for lockscreen controls."
```

---

## Task 7: Adapt Content Pages for Iframe

Modify `library.js` to detect when running inside the shell iframe and delegate play commands to the shell via `postMessage` instead of controlling the `<audio>` element directly. Remove the `AudioPlayer` class and `<audio>` element from `index.html` (they now live in `shell.html`).

**Files:**

- Modify: `library/web-v2/js/library.js`
- Modify: `library/web-v2/index.html`
- Create: `library/tests/test_iframe_bridge.py`

**Step 1: Write the failing test**

Create `library/tests/test_iframe_bridge.py`:

```python
"""Verify library.js delegates play to shell when in iframe."""

from pathlib import Path

LIBRARY_JS = Path(__file__).parent.parent / "web-v2" / "js" / "library.js"
INDEX_HTML = Path(__file__).parent.parent / "web-v2" / "index.html"


class TestIframeBridge:

    def test_library_js_has_postmessage_play(self):
        """library.js must send postMessage to parent when playing."""
        content = LIBRARY_JS.read_text()
        assert "postMessage" in content, (
            "library.js must use postMessage to communicate with shell"
        )

    def test_library_js_detects_iframe(self):
        """library.js must detect if it's running inside an iframe."""
        content = LIBRARY_JS.read_text()
        assert "window.parent" in content or "self !== top" in content or "inIframe" in content

    def test_audio_element_not_in_index(self):
        """<audio> element should not be in index.html (moved to shell.html)."""
        content = INDEX_HTML.read_text()
        assert 'id="audio-element"' not in content, (
            "The <audio> element must be in shell.html, not index.html"
        )

    def test_player_overlay_not_in_index(self):
        """The old player overlay should not be in index.html (replaced by shell player bar)."""
        content = INDEX_HTML.read_text()
        assert 'id="audio-player"' not in content, (
            "The old audio-player overlay must be removed from index.html"
        )

    def test_audio_player_class_removed_from_library_js(self):
        """AudioPlayer class should not be in library.js (moved to shell.js)."""
        content = LIBRARY_JS.read_text()
        assert "class AudioPlayer" not in content, (
            "AudioPlayer class must be removed from library.js (now in shell.js)"
        )

    def test_playback_manager_removed_from_library_js(self):
        """PlaybackManager class should not be in library.js (moved to shell.js)."""
        content = LIBRARY_JS.read_text()
        assert "class PlaybackManager" not in content, (
            "PlaybackManager class must be removed from library.js (now in shell.js)"
        )

    def test_listens_for_player_state(self):
        """library.js should listen for playerState messages from shell."""
        content = LIBRARY_JS.read_text()
        assert "playerState" in content
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/pytest library/tests/test_iframe_bridge.py -v`
Expected: Multiple tests FAIL (AudioPlayer still in library.js, audio-element still in index.html).

**Step 3: Modify library.js and index.html**

This is the largest single change. The modifications are:

**In `library/web-v2/index.html`:**

1. Remove the entire `<!-- Audio Player -->` block (lines 250-288) — the `<div class="audio-player" id="audio-player">` and the `<audio id="audio-element">` element.

**In `library/web-v2/js/library.js`:**

1. **Remove the `AudioPlayer` class** (lines ~2027-2445) and its initialization (`let audioPlayer; ... audioPlayer = new AudioPlayer();` at lines 2448-2451). This entire class is replaced by `ShellPlayer` in shell.js.

2. **Remove the `PlaybackManager` class** (lines ~2903-3088) and its initialization (`let playbackManager; ... playbackManager = new PlaybackManager();` at lines 3092-3095). This class is replaced by position management in shell.js.

3. **Remove the `DuplicateManager` class** — NO. Keep DuplicateManager in place; it's unrelated to the player. Only remove AudioPlayer and PlaybackManager.

4. **Add iframe bridge** at the bottom of `library.js`. This small module:
   - Detects if running in an iframe (`window.self !== window.top`)
   - Provides `shellPlay(book, resume)` that sends a postMessage to the parent
   - Provides `shellPause()`, `shellResume()`, `shellSeek(seconds)`
   - Listens for `playerState` messages from the shell to update UI indicators (e.g., "Now Playing" on book cards)
   - Replaces all existing calls to `audioPlayer.playAudiobook(...)` with `shellPlay(book, true)`

5. **Update all play button handlers** in `AudiobookLibraryV2`. Every place that currently does `audioPlayer.playAudiobook(book, true, event)` or similar should call the bridge function instead. Search for `audioPlayer` references and replace them.

6. **Keep `loadMyLibrary()`** as-is — it doesn't touch the player.

**Step 4: Run test to verify it passes**

Run: `venv/bin/pytest library/tests/test_iframe_bridge.py -v`
Expected: PASS — all 7 tests pass.

**Step 5: Run full test suite**

Run: `venv/bin/pytest library/tests/ -x -q --timeout=60 2>&1 | tail -5`
Expected: All pass. Some tests may need updates if they reference `AudioPlayer` or `PlaybackManager` in library.js.

**Step 6: Commit**

```bash
git add library/web-v2/js/library.js library/web-v2/index.html library/tests/test_iframe_bridge.py
git commit -m "feat: move player to shell, add iframe bridge in library.js

Remove AudioPlayer and PlaybackManager classes from library.js (now
in shell.js). Remove <audio> and player overlay from index.html.
Add postMessage bridge so content pages delegate play/pause/seek
to the shell parent frame."
```

---

## Task 8: Login Redirect & Navigation Links

Update login flow to redirect to `shell.html` instead of `index.html`. Update navigation links that should stay within the iframe. Auth-related links (logout) use `target="_top"`.

**Files:**

- Modify: `library/web-v2/login.html`
- Modify: `library/web-v2/claim.html`
- Modify: `library/web-v2/verify.html`
- Modify: `library/web-v2/index.html` (logout link)
- Create: `library/tests/test_shell_navigation.py`

**Step 1: Write the failing test**

Create `library/tests/test_shell_navigation.py`:

```python
"""Verify login redirects to shell.html and navigation works within iframe."""

from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web-v2"


class TestLoginRedirect:

    def test_login_redirects_to_shell(self):
        content = (WEB_DIR / "login.html").read_text()
        assert "shell.html" in content, (
            "login.html must redirect to shell.html after successful login"
        )
        assert content.count("index.html") == 0 or "shell.html" in content, (
            "login.html should redirect to shell.html, not index.html"
        )

    def test_claim_redirects_to_shell(self):
        content = (WEB_DIR / "claim.html").read_text()
        # claim.html has a link to index.html for "go to library" — should be shell.html
        assert "shell.html" in content

    def test_verify_redirects_to_shell(self):
        content = (WEB_DIR / "verify.html").read_text()
        assert "shell.html" in content


class TestLogoutBreaksIframe:

    def test_logout_uses_target_top(self):
        """Logout link must use target='_top' to break out of iframe."""
        content = (WEB_DIR / "index.html").read_text()
        # The logout button uses JS, but if there are any <a> logout links
        # they should target _top. Check for login.html links with target.
        if 'href="login.html"' in content:
            # Find the login link and check for target
            import re
            login_links = re.findall(r'<a[^>]*href="login\.html"[^>]*>', content)
            for link in login_links:
                assert 'target="_top"' in link, (
                    f"Login link must have target='_top': {link}"
                )
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/pytest library/tests/test_shell_navigation.py -v`
Expected: FAIL — login.html still redirects to index.html.

**Step 3: Update redirects and navigation**

1. **`login.html`** — change all 4 occurrences of `window.location.href = 'index.html'` to `window.location.href = 'shell.html'` (lines 388, 521, 554, 592).

2. **`claim.html`** — change `href="index.html"` to `href="shell.html"` (line 553) and `window.location.href = 'index.html'` to `window.location.href = 'shell.html'` (line 1039).

3. **`verify.html`** — change `href="index.html"` to `href="shell.html"` (line 37) and `window.location.href = 'index.html'` to `window.location.href = 'shell.html'` (line 200).

4. **`index.html`** — add `target="_top"` to the login link (`href="login.html"` at line 49) so clicking "Sign In" from inside the iframe navigates the top-level frame.

**Step 4: Run test to verify it passes**

Run: `venv/bin/pytest library/tests/test_shell_navigation.py -v`
Expected: PASS.

**Step 5: Run full test suite**

Run: `venv/bin/pytest library/tests/ -x -q --timeout=60 2>&1 | tail -5`
Expected: All pass.

**Step 6: Commit**

```bash
git add library/web-v2/login.html library/web-v2/claim.html library/web-v2/verify.html library/web-v2/index.html library/tests/test_shell_navigation.py
git commit -m "feat: redirect login flow to shell.html, add target=_top for auth links

After login, users land in shell.html (which iframes index.html).
Auth links from within the iframe use target='_top' to break out
of the iframe for the full-page login/logout flow."
```

---

## Task 9: Content Page Navigation Within Iframe

Ensure navigation links on content pages (utilities, admin, help, about, contact) work correctly inside the iframe. Links between content pages should stay in the iframe. Links to auth pages should target `_top`.

**Files:**

- Modify: `library/web-v2/utilities.html`
- Modify: `library/web-v2/admin.html`
- Modify: `library/web-v2/help.html`
- Modify: `library/web-v2/about.html`
- Modify: `library/web-v2/contact.html`
- Create: `library/tests/test_content_page_links.py`

**Step 1: Write the failing test**

Create `library/tests/test_content_page_links.py`:

```python
"""Verify content page links work correctly within the iframe."""

import re
from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web-v2"

# Pages that load inside the iframe
CONTENT_PAGES = ["index.html", "utilities.html", "admin.html", "help.html", "about.html", "contact.html"]
# Pages that must break out of iframe
AUTH_PAGES = ["login.html", "register.html", "claim.html", "verify.html"]


class TestContentPageLinks:

    def test_auth_links_have_target_top(self):
        """Links to auth pages from content pages must use target='_top'."""
        for page_name in CONTENT_PAGES:
            page_path = WEB_DIR / page_name
            if not page_path.exists():
                continue
            content = page_path.read_text()
            for auth_page in AUTH_PAGES:
                # Find <a> tags linking to auth pages
                links = re.findall(rf'<a[^>]*href="{auth_page}"[^>]*>', content)
                for link in links:
                    assert 'target="_top"' in link, (
                        f"{page_name}: link to {auth_page} must have target='_top': {link}"
                    )

    def test_js_redirects_to_auth_use_top(self):
        """JS redirects to login.html should use window.top.location."""
        for page_name in CONTENT_PAGES:
            page_path = WEB_DIR / page_name
            if not page_path.exists():
                continue
            content = page_path.read_text()
            # Find window.location.href = 'login.html' patterns
            bad_redirects = re.findall(
                r"window\.location\.href\s*=\s*['\"]login\.html", content
            )
            if bad_redirects:
                # These should be window.top.location.href
                top_redirects = re.findall(
                    r"window\.top\.location\.href\s*=\s*['\"]login\.html", content
                )
                assert len(top_redirects) >= len(bad_redirects), (
                    f"{page_name}: JS redirects to login.html should use "
                    f"window.top.location.href, not window.location.href"
                )
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/pytest library/tests/test_content_page_links.py -v`
Expected: FAIL — links don't have `target="_top"`, JS redirects use `window.location`.

**Step 3: Update content pages**

For each content page, make two types of changes:

1. **HTML `<a>` links to auth pages**: Add `target="_top"` to any `<a href="login.html">` or `<a href="register.html">` links.

2. **JS redirects to auth pages**: Change `window.location.href = 'login.html...'` to `window.top.location.href = 'login.html...'`.

Specific changes per file:

- **`utilities.html`** line 1158: Change `window.location.href = 'login.html?redirect=utilities.html'` → `window.top.location.href = 'login.html?redirect=utilities.html'`
- **`utilities.html`** lines 1166, 1174, 1178, 1184: Change `window.location.href = 'index.html'` → leave as-is (these navigate within iframe, which is correct).
- **`admin.html`**: Any login redirect should use `window.top.location.href`.
- **`help.html`**: Links to `index.html` and `about.html` stay as-is (intra-iframe). The tutorial link `window.location.href = 'index.html?tutorial=1'` stays as-is.
- **`about.html`** and **`contact.html`**: Check for login redirects and update.

Also for each content page: if there are `<a href="login.html">` links, add `target="_top"`.

**Step 4: Run test to verify it passes**

Run: `venv/bin/pytest library/tests/test_content_page_links.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add library/web-v2/utilities.html library/web-v2/admin.html library/web-v2/help.html library/web-v2/about.html library/web-v2/contact.html library/tests/test_content_page_links.py
git commit -m "feat: add target=_top to auth links on content pages

Content pages that load inside the shell iframe need target='_top' on
links to auth pages (login, register) so they navigate the full page
instead of loading inside the iframe."
```

---

## Task 10: Integration — Wire It All Together & Verify

Final integration: ensure the full flow works end-to-end. This task catches any loose ends and runs the complete test suite.

**Files:**

- Possibly modify: any file needing fixes discovered during integration
- Run: full test suite

**Step 1: Run full test suite**

Run: `venv/bin/pytest library/tests/ -x -q --timeout=60 2>&1 | tail -20`
Expected: All tests pass with 0 failures.

**Step 2: Fix any test failures**

If any existing tests reference `AudioPlayer`, `PlaybackManager`, `audio-player`, or `audio-element` in library.js / index.html — update them to reflect the new architecture.

Check specifically:

- `test_player_navigation_persistence.py` — Playwright/Selenium integration test, may need `shell.html` URL
- `test_my_library_ui.py` — may reference player elements
- `test_tutorial.py` — tutorial may reference player UI

**Step 3: Verify no regressions**

Run: `venv/bin/pytest library/tests/ -q --timeout=60 2>&1 | tail -5`
Expected: Same pass/fail/skip ratio as before (1363+ passed, 0 failed, 88 skipped).

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: update tests for shell+iframe player architecture"
```

**Step 5: Final verification checklist**

- [ ] `shell.html` exists and loads
- [ ] `index.html` loads inside the iframe
- [ ] No `<audio>` element in index.html
- [ ] No `AudioPlayer`/`PlaybackManager` class in library.js
- [ ] `credentials: 'include'` on all position API fetch calls (in shell.js)
- [ ] No Audible sync references in library.js
- [ ] `X-Frame-Options: SAMEORIGIN` in core.py
- [ ] `frame-ancestors 'self'` in CSP
- [ ] Login redirects to `shell.html`
- [ ] Auth links use `target="_top"`
- [ ] All tests pass
