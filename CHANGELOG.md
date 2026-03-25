# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

## [7.4.1] - 2026-03-24

### Added

- **Web-based admin user management** (USERS tab in Back Office): create users with TOTP/Magic Link/Passkey auth, change username/email, switch auth method, reset credentials, toggle admin/download roles, delete accounts
- **Last-admin guard**: prevents deletion or demotion of the last remaining admin account
- **Audit logging** for all user management actions: actor, target user, action type, details (JSON), timestamp — stored in `audit_log` table (schema version 7)
- **Paginated, filterable audit log** in Back Office USERS tab with notification badge for new entries
- **Real-time WebSocket push** for audit events to connected admin sessions
- **Self-service My Account modal** in shell header: authenticated users change username, email, auth method, or credentials without admin involvement
- **Admin notification helpers**: in-app badge increment and email notification on critical user actions (role changes, deletions)
- **Granular admin user endpoints** (v7.4+): `PUT /auth/admin/users/<id>/username`, `PUT .../email`, `PUT .../roles`, `PUT .../auth-method`, `POST .../reset-credentials`, `DELETE .../delete`, `GET .../audit-log`
- **Self-service endpoints**: `PUT /auth/user/me/username`, `PUT .../email`, `PUT .../auth-method`, `POST .../reset-credentials` with ownership enforcement
- **Database schema v7**: `audit_log` table (actor_id, target_user_id ON DELETE SET NULL, action, details JSON, created_at) and `last_audit_seen_id` column on users
- **Legacy `DELETE /auth/admin/users/<id>`**: now records audit entry and enforces last-admin guard (parity with new endpoint)
- **Auth fixture helpers** (`conftest.py`): `make_admin_client()`, `make_user_client()`, `create_test_user()` for user management test isolation

### Changed

- **`PATCH /auth/admin/users/<id>`** (legacy edit-profile): now records `update_profile` audit log entry and validates username max-length 24 chars
- **USERS tab JS**: edit modal now calls granular endpoints (`/username`, `/email`) instead of the legacy combined PATCH, producing per-field audit entries
- **Username validation**: max length enforced as 24 characters (ASCII printable, 3–24 chars) consistently across backend schema, API validation, and client-side guards in `admin.js` and `account.js`
- **`SCHEMA_VERSION`** bumped to 7 for `audit_log` table and `last_audit_seen_id` column

### Fixed

- **WebSocket double handshake**: Removed `geventwebsocket.handler.WebSocketHandler` from direct `api_server.py` execution — `flask_sock` handles WebSocket upgrades natively; both handlers together caused duplicate 101 responses that corrupted WebSocket framing
- **Proxy WebSocket blocking**: Changed `proxy_server.py` from single-threaded `HTTPServer` to `ThreadingHTTPServer` — active WebSocket tunnels no longer block all other HTTP requests
- **SSL buffer starvation in proxy WebSocket tunnel**: Added `ssl.SSLSocket.pending()` check before `select.select()` — heartbeats arriving through TLS were stuck in the SSL decryption buffer, invisible to `select()`, causing server-side receive timeouts
- **Live Connections always showing 0**: Fixed race condition where `/api/admin/connections` was fetched before WebSocket had time to register; now uses delayed initial fetch, event-driven refresh on WebSocket open, 30-second polling, and refresh on Activity tab click
- **upgrade.sh backup cleanup**: Root-owned `.pyc` files in old backups caused `rm -rf` to fail under `set -e`, silently aborting the upgrade before file sync; added sudo fallback
- **Admin delete endpoint**: JS `usersTab.js` was calling the wrong path (`/delete` missing); now targets the audited `/delete` route correctly
- **Toggle admin/download endpoints**: `toggle_user_admin` and `toggle_user_download` now record audit entries for each role change
- **Legacy `AccessRequestRepository` methods**: relocated from `UserRepository` to correct class; fixed test isolation for `access_requests` table
- **Self-deletion guard**: `admin_delete_user_v2` now checks if actor is deleting their own account and rejects with 400 before last-admin check
- **Access request cleanup**: deleting a user now also removes their pending access requests to prevent orphaned entries
- **Stale docstrings**: `3-32 chars` corrected to `3-24 chars, ASCII printable` in username constraint documentation
- **TOTP QR code display**: Embedded base64 PNG in 6 API endpoints that return TOTP setup data; frontend now uses data: URI for display and download instead of a non-existent image endpoint
- **Unified account button**: Consolidated two separate user buttons (shell.html header + index.html iframe) into a single account button in the shell header with full modal (profile, auth management, contact admin, sign out, delete account)
- **upgrade.sh service restart**: Added root UID detection so `systemctl` runs properly when upgrade.sh is invoked via `sudo` (services were silently not restarting)
- **Stack-trace exposure**: Auth health check endpoint no longer returns raw exception strings in error responses
- **Cache busting**: All HTML files now use consistent `?v=` timestamps across CSS/JS references
- **Tutorial outdated references**: Updated tutorial step targeting removed `#user-menu` element to reference the account button in the header bar
- **Help page**: Updated "Your Profile" section to reflect the new My Account modal
- **CodeQL false positive suppression**: Added `lgtm[]` inline comments to prevent recurring alerts for test code (verify=False, chmod 777) and known-safe production patterns (flask-debug, path-injection)
- **Account panel QR code rendering**: `showSetupResult()` in account.js now displays the QR code image when changing auth method to TOTP (was previously text-only)
- **Dead code cleanup**: Removed ~135 lines of orphaned `.user-menu` CSS from auth.css and dead `getElementById("user-menu")` calls from library.js after v7.4.0 UI migration
- **Exception information exposure**: Duplicate checksum endpoints in duplicates.py no longer return raw `str(e)` in error responses

## [7.3.0.1] - 2026-03-23

### Added

- **Art Deco themed error pages** for Caddy reverse proxy: maintenance (upgrade) page with librarian-on-ladder bookshelf scene and sliding progress bar; unavailable page with librarian-pushing-cart scene and blinking status indicator
- **Generic unavailable page** (`caddy/unavailable.html`) — shown by Caddy on 502/503 when backend is unreachable, with JS health polling (5s) and meta-refresh (30s) fallback for auto-recovery

### Changed

- **Maintenance page** (`caddy/maintenance.html`) restyled from plain dark theme to full Art Deco design matching the app (sunburst panel, gold chevron border, diamond lattice background, Optima font, gold/brass/cream palette)

## [7.3.0] - 2026-03-22

### Added

- **Mandatory preflight check system** (LEAPP-inspired): Upgrades require preflight validation before execution
- **Always-on backup with rolling retention** (keep last 5): Backup is no longer optional
- **`--skip-service-lifecycle`** internal flag for helper-owned lifecycle
- **Full upgrade feature parity in web UI**: Force, major version, and specific version fields
- **GET `/api/system/upgrade/preflight`** endpoint with staleness computation
- **Preflight gate on POST `/api/system/upgrade`**: Blocks upgrades without valid preflight (unless force)
- **Caddy maintenance page** for external visitors during upgrade (auto-reloads via health polling)
- **Resilient browser upgrade overlay** with 9-step progress tracking (tolerates API downtime)
- **Upgrade consistency enforcement rule** (`.claude/rules/upgrade-consistency.md`)

### Changed

- **Helper lifecycle** rewritten to 9-step orchestration with status file durability
- **`--backup` flag** is now a no-op (backup always runs)
- **Upgrade overlay** uses textContent/DOM APIs exclusively (no innerHTML)

### Fixed

- **Service name bug in upgrade-helper-process**: `audiobooks-*` (plural) → `audiobook-*` (singular) — ALL web-triggered service operations were silently failing
- **CSP headers**: Added `wss:` and `ws:` WebSocket schemes to `connect-src` in proxy_server.py (blocked WebSocket connections in strict CSP environments)
- **SQL injection**: Parameterized raw string interpolation in maintenance.py `_get_history()` and `_get_windows()` queries
- **Flask debug bind**: Fixed `app.run(host='localhost')` to `app.run(host='127.0.0.1')` in maintenance.py — `localhost` may resolve to `::1` on IPv6 systems
- **python-security.yml**: Fixed pip-audit invocation path to use venv pip directly
- **ASIN GLOB test**: Corrected `test_audiobooks_extended.py` ASIN pattern from `B0*` to `[AB][0-9A-Z]*` to match real Audible ASIN format
- **Dev DB orphaned refs**: Cleaned stale foreign-key references in audiobooks-dev.db (orphaned edition, position, and hash records)
- **`library/launch-v3.sh`**: Deleted deprecated launch script (replaced by `audiobook.target` systemd service)

## [7.2.1.1] - 2026-03-21

### Added

- **`upgrade.sh --major-version`**: New flag for major version upgrades — forces venv rebuild (removes old deps like waitress, installs new ones), runs config migrations, enables new services
- **`upgrade.sh` audit and cleanup**: Every upgrade now scans for and fixes broken symlinks, orphaned systemd units, stale legacy files, and deprecated config variables
- **`install.sh --fresh-install`**: Reinstall from scratch while preserving audiobook library and user settings (ports, auth, data dirs)
- **Config migration system**: `config-migrations/` directory with numbered idempotent scripts that add new config variables to existing installations
- **`show_usage()`**: Both `upgrade.sh` and `install.sh` now show comprehensive formatted help with `--help`, `-h`, or no arguments

### Changed

- **Documentation**: All Waitress references updated to Gunicorn+geventwebsocket across README, ARCHITECTURE, INSTALL.md, install-services.sh, proxy_server.py
- **`launch-v3.sh`**: Marked as deprecated with notice pointing to systemd services
- **`audiobooks.conf.example`**: Added `AUDIOBOOKS_RUN_DIR` setting

## [7.2.1] - 2026-03-21

### Fixed

- **Maint Sched tab never initialized**: MutationObserver watched `style` attribute changes but the tab system uses `classList.toggle("active")` — `initMaintSched()` was dead code, so task dropdown was always empty and schedule toggle never worked
- **Task Type dropdown empty**: Added `credentials: "same-origin"` to all fetch calls, placeholder option, and error state instead of silent `.catch()`
- **Dates labeled "(UTC)"**: Removed misleading label — `datetime-local` input works in the user's local timezone; added helper text clarifying automatic conversion
- **Datetime picker not discoverable**: Added explicit "Pick" button calling `showPicker()` and styled `::-webkit-calendar-picker-indicator` with `invert` filter for dark theme visibility
- **Cron scheduling hidden**: Promoted from buried radio button under Recurring to top-level "Cron (advanced)" dropdown option with inline format examples
- **Announcement banner not activating**: `sendMessage()` now dispatches `CustomEvent` directly after POST; added `visibilitychange` re-fetch for tab-switch catch-up

### Changed

- **Cache-Control headers**: Proxy now sends `no-cache` for HTML (revalidate each request), `immutable, max-age=1yr` for versioned JS/CSS (`?v=`), `max-age=5min` for unversioned JS/CSS, `max-age=1day` for images/fonts
- **Cache-busting**: Added `?v=` parameters to all new JS/CSS assets (maint-sched.js, websocket.js, maintenance-banner.js, maintenance-banner.css)

## [7.2.0] - 2026-03-21

### Added

- **Maintenance scheduling system**: Cron-based automated task execution with 5 built-in tasks (db_vacuum, db_integrity, db_backup, library_scan, hash_verify)
- **WebSocket infrastructure**: Migrated from Waitress to Gunicorn+geventwebsocket for real-time bidirectional communication
- **Maintenance announcement banner**: Pulsing indicator with expandable panel, SVG knife switch dismiss control, Web Audio API synthesized sounds
- **Maintenance scheduler daemon**: `audiobook-scheduler.service` with file lock, notification queue, and graceful shutdown
- **Admin Maint Sched tab**: Full CRUD for maintenance windows, manual announcements, execution history
- **Notification bridge**: Gevent greenlet polls DB every 5s, broadcasts to WebSocket clients
- **Proxy WebSocket tunneling**: Raw TCP socket relay in proxy_server.py for WebSocket upgrade requests

### Changed

- **API server**: Migrated from Waitress to Gunicorn with `GeventWebSocketWorker` (`-w 1` hard constraint for in-memory connection manager)
- **Docker entrypoint**: Updated from Waitress to Gunicorn startup
- **Requirements**: Replaced `waitress` with `gunicorn`, `gevent`, `gevent-websocket`, `flask-sock`, `croniter`

## [7.1.3.4] - 2026-03-20

### Fixed

- **Systemd restart resilience**: Increased `StartLimitIntervalSec` from 60s to 300s and `RestartSec` to 30s across all long-running services (api, proxy, redirect, converter, mover) — services that depend on slow RAID array mounts were hitting the start limit and locking out permanently at boot

## [7.1.3.3] - 2026-03-19

### Changed

- **Play always resumes**: Play button now always resumes from the user's last saved position — removed the separate Resume button from all views (grid cards, book detail modal, edition view, grouped view) as unnecessary UX clutter
- **Position threshold lowered**: Save/resume position threshold reduced from 30 seconds to 5 seconds across the entire stack (frontend save guards, frontend position reader, backend API validation)

### Fixed

- **Position lost on scrub/skip**: Scrubbing the progress bar, pressing +30s/-30s skip buttons, or using media session seek controls now immediately saves position to localStorage and queues API sync — previously these operations only modified `audio.currentTime` without persisting, so closing the browser after scrubbing while paused would lose the position entirely
- **Position save interval**: Reduced localStorage save interval from 30s to 5s and API save delay from 15s to 5s for more frequent persistence during playback
- **CRLF injection sanitization**: Strip `\r` and `\n` from query string in `proxy_server.py` redirect to prevent HTTP response splitting (CodeQL #315)
- **Docker CVE pins**: Add `pyopenssl>=26.0.0` (CVE-2026-27448, CVE-2026-27459) and bump `pyasn1>=0.6.3` in `requirements-docker.txt`
- **Unused import**: Remove unused `auth_if_enabled` import from `utilities_system.py`

## [7.1.3.2] - 2026-03-18

### Fixed

- **Resume button missing on mobile**: Scoped `.btn-resume` and `.btn-download` CSS hide rules to `.book-card` only — the book detail modal Resume button was hidden on all mobile browsers (Android Chrome/Brave/Firefox, iOS Safari/iPadOS) due to unscoped `display: none !important` in responsive breakpoints
- **Book detail modal obscured by browser chrome**: Changed modal from bottom-sheet (`align-items: flex-end`) to centered layout with safe area inset padding on all edges — the Play/Resume buttons were hidden behind mobile browser navigation bars
- **Comprehensive safe area audit**: Added `env(safe-area-inset-*)` and `--browser-chrome-bottom` protection to all fixed/edge-touching elements across 8 CSS files: modals, sidebar, auth pages, user dropdown menu, backoffice toasts/modals, help tooltips, tutorial tooltips, and help page

## [7.1.3.1] - 2026-03-18

### Fixed

- **Version endpoint auth**: Removed `@auth_if_enabled` from `/api/system/version` — the About page fetches version without authentication, so the endpoint must be public. Previously displayed "Unknown" for unauthenticated visitors
- **Path info leak**: Removed `project_root` from version API response — internal filesystem paths should not be exposed to unauthenticated users

## [7.1.3] - 2026-03-18

### Fixed

- **Mobile viewport**: Shell layout uses `visualViewport` API with dynamic `--app-height` and `postMessage` to communicate browser chrome offset to iframe, preventing mobile browser bottom bars from obscuring UI (Resume button, scrubber, player controls)
- **Clean URL (complete fix)**: All auth pages (`login.html`, `verify.html`, `claim.html`) and autoplay redirect now navigate to `/` instead of `shell.html`, ensuring browser address bar always shows the clean canonical URL
- **Proxy query string handling**: `proxy_server.py` now uses `urlparse` to separate path from query string, correctly serving `/?autoplay=...` and preserving query strings across `/shell.html` → `/` redirects
- **Continue badge removed**: Removed "Continue" text badge overlay from book cover art images — the Resume button is sufficient indication of saved position

## [7.1.2.1] - 2026-03-17

### Added

- **Cover Art Resolver**: New tiered external cover art resolver (`scanner/utils/cover_resolver.py`) fetches missing covers from Audible CDN (by ASIN), Open Library API, and Google Books API as a fallback when embedded and sidecar covers are absent

### Changed

- **Clean URL**: Web proxy now serves shell.html content directly at `/` instead of 302 redirect; `/shell.html` returns 301 → `/` for clean browser URLs

### Fixed

- **Mobile player clipping**: Added `env(safe-area-inset-bottom)` CSS padding to shell player to prevent bottom browser chrome from covering the scrub bar on mobile devices

## [7.1.2] - 2026-03-16

### Fixed

- **Critical: Position save reset bug** — Playback position could reset to 0:00 when auto-save fired at the start of playback (e.g., pausing then resuming "A Dirty Job" lost 6h+ of progress). Root cause: `onTimeUpdate()` auto-save guard used `> 0` instead of `> 30`, allowing near-zero positions (168ms) to overwrite real saved positions when audio restarts from the beginning
- **Frontend defense-in-depth** — All position save paths (auto-save, pause, book-switch, page-close, `savePosition()`, `flushToAPI()`) now guard against positions < 30s, consistent with the read-side filter
- **Backend defense-in-depth** — API endpoint `PUT /api/position/<id>` now rejects positions 1–29999ms with HTTP 422, preventing near-zero positions from reaching the database regardless of frontend behavior (position 0 is still allowed for intentional clear on book completion)
- **Reset auto-save timer on book switch** — `_lastSaveTime` is now reset in `playBook()` so auto-save doesn't fire immediately with a stale timer when restarting a book

## [7.1.1.1] - 2026-03-16

### Changed

- **Cloudflare credentials**: Cache purge script and upgrade.sh now source credentials from `~/.config/api-keys.env` (shared with cloudflare-manager) instead of requiring a dedicated token file at `/etc/audiobooks/cloudflare-api-token`
- **Zone ID hardcoded**: thebosco.club zone ID is now hardcoded, eliminating an unnecessary API lookup on every purge
- **Config cleanup**: Removed `CF_TOKEN_FILE` from `audiobook-config.sh` (no longer needed)

## [7.1.1] - 2026-03-16

### Added

- **3D Cuboid Buttons**: All buttons throughout the entire UI now have a pronounced 3D cuboid appearance with visible colored wall shadows (dark brown walls, gold highlights) — main library, back office, admin panel, player, sidebar, modals, help, tutorial, auth, and shell pages
- **Tab Color Identity**: BROWSE ALL tab has distinct gold identity; MY LIBRARY tab has distinct emerald identity for visual differentiation
- **Cloudflare Cache Purge Script**: New `audiobook-purge-cache` standalone script for manual CDN cache purging with auto-detected zone ID, selective URL purging, and quiet mode for scripting
- **Upgrade Cache Purge**: `upgrade.sh` now automatically purges Cloudflare CDN cache after both local and remote deployments (non-fatal — skips if no API token configured)
- **Config Variables**: Added `CF_TOKEN_FILE` and `CF_ZONE_ID` to `audiobook-config.sh` for Cloudflare CDN integration

### Changed

- **3D Shadow System**: Replaced invisible `rgba(0,0,0,...)` box-shadows with colored wall shadows (`#4a3520`, `#6b5030`, `#3a2810`) that are visible on the dark Art Deco theme
- **Cache Busters**: Switched from semantic version strings (`?v=7.1.3`) to timestamp-based cache busters (`?v=1773686866`) across all 13 HTML files and all CSS `@import` chains to reliably bypass Cloudflare CDN caching

### Fixed

- **CSS Cache Chain**: Fixed `@import` cache-buster mismatch where HTML `<link>` tags had updated versions but inner CSS `@import` statements still referenced old versions, causing Cloudflare to serve stale theme CSS
- **Scrub Bar & Position Saving**: Fixed player scrub bar, position saving, and Resume button functionality (committed in prior session as d548ed2)

## [7.1.0] - 2026-03-14

### Added

- **Help Page**: Added FAQ section with 10 common questions about the library (multi-device sync, downloads, grouped vs sorted view, collections, media keys, etc.)
- **Help Page**: Added Grouped View documentation to the sorting section explaining collapsible author/narrator headers
- **Tutorial**: Updated Sort Options step to mention grouped view feature

### Changed

- **Documentation**: Comprehensive audit of all documentation for v7.0.0-v7.0.2 changes — updated README, ARCHITECTURE, CONTRIBUTING, INSTALL, QUICKSTART, UPGRADE_GUIDE, and API docs with multi-author normalization, grouped views, admin endpoints, and normalized database tables
- **Documentation**: Fixed stale port references (5000 → 5001) and removed obsolete `api.py` references in library docs
- **Description**: Updated project description to reflect removal of Audible sync (removed in v6.3.0); now reads "Self-hosted audiobook library browser with conversion, web player, and optional Audible downloading"
- **Help Page**: Updated cache busters from v7.0.0 to v7.0.2

## [7.0.2] - 2026-03-14

### Fixed

- Fixed `upgrade.sh --from-github` failing with "Invalid target directory" due to 0-indexed array bug in `find_installed_dir()` (`${found[1]}` → `${found[0]}`)

## [7.0.1] - 2026-03-14

### Fixed

- Updated `install-manifest.json` with BTRFS subvolume entries for `/var/lib/audiobooks` and `/etc/audiobooks`
- Updated ARCHITECTURE.md recommended subvolume layout to reflect NVMe-backed state and config directories
- Resolved all 343 E501 line-too-long violations across 70+ files (88 char limit)
- Added `defusedxml` for safe XML parsing in librivox_downloader.py (S314 security fix)
- Fixed `from library.` import paths that failed under pytest (ModuleNotFoundError)
- Updated hardcoded-path test scanner to handle multi-line `environ.get()` calls

## [7.0.0] - 2026-03-13

### Added

- **Multi-author/narrator normalization**: New `authors`, `narrators`, `book_authors`, and `book_narrators` tables provide proper many-to-many relationships. Books with multiple authors (e.g., "Stephen King, Peter Straub") now have each author as a separate, sortable entity.
- **Name parser module** (`name_parser.py`): Three-tier metadata extraction — structured tags, delimiter splitting (semicolons, "and", "&"), and comma disambiguation ("Last, First" vs "Author1, Author2"). Handles group names (Full Cast, BBC Radio), compound last names (de, van, von, le), and role suffixes.
- **Grouped sort view**: New "Author (Grouped A-Z)" and "Narrator (Grouped A-Z)" sort options in the frontend. Books appear under collapsible author/narrator headers with Art Deco styling. Multi-author books appear under each author group.
- **Grouped API endpoint**: `GET /api/audiobooks/grouped?by=author|narrator` returns books grouped by normalized author/narrator with sort_name ordering and orphan "Unknown" group.
- **Enriched flat API**: `/api/audiobooks` response now includes `authors` and `narrators` arrays alongside existing flat string fields for backward compatibility.
- **Admin correction endpoints**: `PUT /api/admin/authors/<id>` (rename), `POST /api/admin/authors/merge` (merge duplicates), `PUT /api/admin/books/<id>/authors` (reassign). Symmetric endpoints for narrators. All operations regenerate flat text columns.
- **Schema migration** (`011_multi_author_narrator.sql`): DDL for normalized tables with foreign keys and indices. Applied automatically during `upgrade.sh`.
- **Data migration** (`migrate_to_normalized_authors.py`): Idempotent script parses flat author/narrator columns and populates junction tables. Group name redirection ensures entities like "Full Cast" are always classified as narrators.

### Changed

- **Author/narrator sidebar filter**: Now displays individual names from normalized `authors`/`narrators` tables instead of raw composite strings from flat columns. 572 individual authors (was 138 composite strings). Sorted by last name.
- **Author/narrator book filtering**: Selecting an author in the sidebar now uses junction table JOINs for exact matching, finding all books linked to that author — including multi-author books.
- **Narrator counts**: `/api/narrator-counts` now uses normalized narrator table for accurate per-narrator book counts.
- **upgrade.sh**: Now detects and applies database schema migrations automatically. Checks for `authors` table existence before applying DDL, runs data migration if tables are empty. `--force` flag now forwarded to remote SSH deploys.
- **Cache busting**: All CSS/JS version strings updated to `v=7.0.0` across all HTML files and CSS `@import` chain.

### Fixed

- **Role suffix exclusion**: Names with role suffixes (translator, editor, foreword, illustrator, etc.) are excluded from the authors table during migration. 43 entries like "Frances Riddle - translator" properly filtered.
- **Schema migration reliability**: `apply_schema_migrations()` extracted as standalone function, runs even when versions are identical (handles cases where code was deployed but migration didn't run). Fixed quote stripping from `AUDIOBOOKS_DATABASE` config value.

## [6.7.2.4] - 2026-03-05

### Fixed

- **UTC timezone label**: Invitation timestamps in admin UI now display "UTC" suffix so admins don't mistake UTC times for local time
- **Cache-buster sync**: Updated `?v=` query strings to 6.7.2.4 across all 12 HTML files and CSS `@import` chain (stale at 6.7.2.2, caused browser to serve cached JS without invitation timestamp feature)

## [6.7.2.3] - 2026-03-05

### Added

- **Back office invitation timestamps**: Admin user list now shows "Invited: [date]" and "Expires/Expired: [date]" for users who haven't logged in yet, replacing the uninformative "Never" label. Expired invitations display in red, pending ones in yellow.
- **API invitation data**: `/auth/admin/users` endpoint now includes `invite_expires_at` and `invite_expired` fields for unclaimed invitations, sourcing expiry from `pending_recovery` (magic link) or `access_requests` (TOTP/passkey)

## [6.7.2.2] - 2026-03-03

### Fixed

- **Marquee ticker mode**: When few new books don't fill the viewport, marquee now uses a single-pass news-ticker scroll (right-to-left, no visible duplication) instead of the 2-copy infinite loop. Classic seamless scroll still activates when content fills or overflows the viewport.
- **Cache-buster sync**: Updated `?v=` query strings to 6.7.2.2 across all 12 HTML files and CSS `@import` chain (were stale at 6.6.3 or 6.7.2)
- **Backup code recovery**: Fixed login.html backup code form calling non-existent `/auth/login/backup` — now correctly calls `/auth/recover/backup-code` and displays new TOTP credentials
- **JS formatting**: Auto-formatted all 7 JavaScript files with Prettier for consistent style

## [6.7.2.1] - 2026-03-03

### Fixed

- **Marquee viewport fill**: New books marquee now measures one cycle's width and repeats content enough times to always overflow the viewport, eliminating visible duplication when only 1-2 books are new

## [6.7.2] - 2026-03-02

### Added

- **Mobile book detail bottom-sheet**: Tapping compact book cards on mobile (≤480px or landscape) opens a slide-up modal showing full details — cover art, title, author, narrator, format, duration, progress, and Play/Resume/Download buttons
- **Compact card tap handler**: Event delegation on book grid detects compact layout via `matchMedia` and routes taps to the detail modal instead of default card behavior

### Fixed

- **Double-click Play bug**: Fixed the root cause of needing to click Play twice — `shellPlay()` now preserves play intent via `sessionStorage` when redirecting from bare `index.html` to `shell.html`, and `shell.js` picks up the autoplay parameter on load
- **Player bar layout shift**: Shell player bar now overlays content (position: fixed, z-index: 9999) instead of shrinking the iframe from 100% to `calc(100% - 80px)`, eliminating the visual "refresh" that users perceived as the first click failing
- **Dev Caddyfile**: Changed `try_files` fallback from `/index.html` to `/shell.html` (correct entry point) and `X-Frame-Options` from `DENY` to `SAMEORIGIN` (allows same-origin iframe embedding)
- **Landscape media query desktop leak**: Added `max-width` constraints to landscape-orientation media queries (`960px` and `1024px`) so they no longer match desktop browser windows that happen to have a landscape aspect ratio

## [6.7.1.5] - 2026-03-01

### Fixed

- **Audit fixes**: Version sync across Dockerfile, install-manifest, SECURITY.md; Docker env var bridging for `HTTP_REDIRECT_PORT`/`HTTP_REDIRECT_ENABLED`; shellcheck fixes (SC2076, SC2064, SC2120); auth.db backup retention (keep 5); `LimitNOFILE=65536` for API service; marshmallow minimum bumped to >=4.0.0
- **Test coverage**: Fixed 2 skipped supplement scan tests — replaced stale mock targets with real filesystem operations, bringing test count from 1415 to 1417
- **Code formatting**: 16 Python files reformatted with ruff

## [6.7.1.4] - 2026-03-01

### Fixed

- **Mobile responsive overhaul**: Complete rework of phone viewport layouts — portrait shows 4-column dense grid with 40px icon covers, wrapping non-bold titles, and only title/author/play visible. Landscape (≤700px height) shows 10-column dense grid with same compact treatment. Small phones (≤360px) inherit compact rules cleanly. Desktop unchanged.
- **CSS cascade priority**: Fixed responsive.css overrides being ignored on mobile — `@import` cascade order caused library.css base rules to win at equal specificity. All mobile overrides now use `!important` to compensate (documented in CSS comments).
- **Dead CSS cleanup**: Removed conflicting card/cover/title rules in Section F (≤360px) that were overridden by Section E2's `!important` rules at ≤480px.

## [6.7.1.3] - 2026-02-28

### Fixed

- **Play/resume plumbing**: Fixed `shellPlay()` ignoring the `resume` parameter — both Play and Resume buttons previously did the same thing. Resume now correctly restores saved position; Play starts fresh. Same-book unpause short-circuits without reloading.
- **upgrade.sh service restart safety**: Added EXIT trap with dirty-flag pattern so services always restart if the script dies mid-upgrade (previously `set -e` could kill the script between `stop_services` and `start_services`, leaving services dead — caused production 502)
- **Genre scanner→importer disconnect**: Connected scanner genre output (`genre` field) to importer genre input (`genres` field) so scanned genre data actually populates the database
- **NAMESPACE crash**: Prevented API service crash when `library/data` directory is missing on fresh installs

## [6.7.1.2] - 2026-02-27

### Fixed

- **Shell script permissions**: `upgrade.sh` now ensures all `.sh` files are world-readable (755) after upgrade — fixes `/etc/profile.d` scripts failing to `source` shared libraries like `audiobook-config.sh` when permissions were 711

### Changed

- **ARMv7 homage**: Added a tribute to 32-bit ARM users in README

## [6.7.1.1] - 2026-02-27

### Changed

- **Drop ARM/v7 platform support**: Removed `linux/arm/v7` from Docker multi-arch builds — `sqlcipher3` package's Conan build system does not support the armv7l architecture, causing CI build failures on v6.7.0.2 and v6.7.1
- **Docker platforms**: Now builds for `linux/amd64` and `linux/arm64` only (x86-64 and 64-bit ARM including Apple Silicon, Raspberry Pi 3/4/5)

### Fixed

- **Docker CI build failure**: Resolved `sqlcipher3` arm/v7 build error that blocked Docker image publishing for v6.7.0.2 and v6.7.1

## [6.7.1] - 2026-02-27

### Fixed

- **Cloudflare 524 timeout**: Resolved origin server timeout when navigating from audio player back to library — Waitress thread starvation caused by audio streaming holding worker threads during playback
- **SQLite WAL mode**: Enabled Write-Ahead Logging on all API database connections so position sync writes no longer block library page reads
- **SQLite performance**: Added `synchronous=NORMAL`, `cache_size=8MB`, and `busy_timeout=5s` pragmas across all API connection factories
- **N+1 query elimination**: Replaced per-book query loop in `GET /api/audiobooks` (300 queries per page) with 6 batch queries using `WHERE IN` for genres, eras, topics, supplements, and edition detection
- **Thread capacity**: Increased Waitress worker threads from 4 to 16 to handle concurrent audio streams alongside API requests

## [6.7.0.3] - 2026-02-27

### Fixed

- **Play button regression**: Resolved play button failures in shell+iframe architecture — buttons (play, pause, resume, download) stopped working between v6.6.6.1 and v6.7.0.2
- **postMessage bridge**: Replaced cross-frame postMessage with direct `window.parent.shellPlayer` access (Cloudflare proxy chain blocked postMessage)
- **Shell player scope**: Changed `let shellPlayer` to `var shellPlayer` so it's accessible as a `window` property from the iframe (root cause of two-click play bug)
- **Book property normalization**: Normalized API property names (`id`/`cover_path` vs `bookId`/`coverUrl`) in shell player
- **User gesture window**: Reordered `playBook()` to call `audio.play()` before async `getBestPosition()` to preserve browser autoplay gesture activation
- **Button text scaling**: Added CSS container queries with `clamp()` font-size to prevent button text truncation on narrow cards
- **Audio CORS**: Removed unnecessary `crossOrigin='anonymous'` attribute on audio element (same-origin streaming)
- **Cache-busting**: Bumped query params to `?v=6.7.0.5` across shell.html and index.html to bypass Cloudflare edge cache

## [6.7.0.2] - 2026-02-26

### Added

- **Streaming-only note**: Help page Audio Player section now explains that The Library streams from server storage and cannot access user-side files, with link to Downloads section
- **Offline player recommendations**: Help page Downloads section recommends audiobook players per platform (VLC, foobar2000, IINA, Celluloid, BookMobile, Smart AudioBook Player) for Windows, Mac, Linux, iOS, and Android
- **Download tutorial step**: Interactive tutorial now includes an optional step for the Download button explaining the streaming-only design

### Changed

- **Download button tooltip**: Updated to explain streaming-only design and recommend local players for offline listening

## [6.7.0.1] - 2026-02-25

### Added

- **Docker ARM/v7 support**: Docker images now build for linux/amd64, linux/arm64, and linux/arm/v7 (covers Raspberry Pi and other 32-bit ARM devices)

### Changed

- **Production data isolation rules**: Clarified test/QA isolation boundary — no live mounts to production storage (copies are fine); added release leak prevention safeguards for licensed content

## [6.7.0] - 2026-02-25

### Added

- **Persistent Player**: New shell+iframe architecture — audio playback persists across page navigation. `shell.html` wraps content in an iframe while keeping `<audio>` element and player controls in the parent frame
- **Shell Player**: Full-featured player bar with play/pause, rewind/forward 30s, speed cycling (0.5-2.5x), volume, progress scrubbing, close button
- **Media Session API**: Lockscreen/notification controls for audio playback
- **postMessage Bridge**: Bidirectional communication between shell and iframe content pages for play/pause/seek/playerState
- **iframe Link Safety**: Auth page links from content pages use `target="_top"` to navigate out of iframe; JS redirects use `window.top.location.href`

### Changed

- **Security Headers**: X-Frame-Options changed from `DENY` to `SAMEORIGIN`; CSP updated with `frame-ancestors 'self'` and `frame-src 'self'` to allow same-origin iframe embedding
- **Login Flow**: Auth pages (login, claim, verify) now redirect to `shell.html` instead of `index.html`

### Fixed

- **My Library**: Added `credentials: 'include'` to `savePositionToAPI()` and `getPositionFromAPI()` — session cookies were not sent, server saw unauthenticated requests, and My Library appeared empty for all users
- **Button Spinners**: Fixed `.button-loading` spinners visible before user interaction on login/claim pages — `display: inline-flex` was overriding HTML `hidden` attribute

### Removed

- **Audible Sync**: Removed dead frontend Audible sync code (~80 lines) — `syncWithAudible()`, timer management, and all call sites. Backend was already cleaned up
- **AudioPlayer/PlaybackManager**: Removed from `library.js` (moved to `shell.js` as `ShellPlayer`); removed `<audio>` element and player overlay from `index.html`

## [6.6.7] - 2026-02-25

### Fixed

- **Admin UI**: Resolved audiobook titles in admin activity log — denormalized title into auth DB at event time instead of relying on cross-DB ID lookups that break after library reimport

## [6.6.6.1] - 2026-02-25

### Fixed

- **Upgrade**: Service detection grep pattern `audiobooks` → `audiobook-` to match actual unit names — stop/start were silently skipping all service management during upgrades
- **Upgrade**: Added `*.target` to systemd unit file deployment glob — `audiobook.target` was not being copied to `/etc/systemd/system/`
- **Scripts**: Converted 28 remaining zsh `read -r "var?prompt"` instances to bash `read -r -p "prompt" var` across install.sh (13), uninstall.sh (3), upgrade.sh (2), install-user.sh (4), migrate-api.sh (1), install-services.sh (4) — interactive prompts were silently broken since bash conversion
- **CI**: Removed stale "Install zsh" step from release.yml (all scripts use bash since v6.6.5)

## [6.6.6] - 2026-02-24

### Changed

- **Auth**: Username limits changed from 5-16 to 3-24 characters — updated SQL schema, migrations, Python backend, CLI, JavaScript frontend, HTML forms, and tests

### Fixed

- **Scripts**: Replaced all zsh `${0:A:h}` syntax with bash `$(dirname "$(readlink -f "$0")")` in 9 wrapper scripts — broke all CLI commands when called via symlinks
- **Scripts**: Removed `local` keyword outside function scope (SC2168) in `move-staged-audiobooks` and `monitor-audiobook-conversion` — `local` only works inside functions in bash
- **Scripts**: Fixed `find-duplicate-sources` crash on empty Sources directory — empty associative array triggered `set -u` unbound variable error
- **Scripts**: `audiobook-help` now sources config to display resolved paths instead of literal `$VARIABLE` names
- **Install**: Fixed `((issues_found++))` → `((issues_found++)) || true` in `install.sh` (8 locations) and `migrate-api.sh` (3 locations) — bash arithmetic `((0++))` returns exit code 1, killing scripts under `set -e`
- **Install**: `chown /var/log/audiobooks` for audiobooks user, source files get 644 and shell scripts get 755 permissions on deploy
- **Install**: Fixed `lib/audiobook-config.sh` permissions from 711 to 755; fixed 21 source files from 600 to 644
- **Install**: Added `chown audiobooks:audiobooks` for data subdirectories (`Library/`, `Sources/`, `Supplements/`) — were created as root-owned, blocking service writes
- **Install**: `embed-cover-art.py` wrapper now uses venv Python for mutagen dependency instead of system Python
- **Install**: Added `DATA_DIR="/var/lib/audiobooks/data"` to generated `audiobooks.conf` and create `.index` directory with proper ownership
- **Install**: Wrapper script templates in `install.sh` and `install-user.sh` now generate `#!/bin/bash` shebangs
- **Manifest**: Fixed `install-manifest.json` DB path to `/var/lib/audiobooks/audiobooks.db`
- **Converter**: Fixed critical `$AAXC_FILE` → `$SOURCE_FILE` variable in 3 locations — was causing stale queue entries and re-processing of already-converted files
- **Converter**: Changed shebang to `#!/bin/bash` (required for `export -f` function exports)
- **Services**: Added `RequiresMountsFor=/srv/audiobooks` to mover service to prevent race conditions at boot
- **Systemd**: Moved `StartLimitIntervalSec`/`StartLimitBurst` from `[Service]` to `[Unit]` section in `audiobook-api.service` — systemd was ignoring these directives with warnings when placed in `[Service]`
- **Systemd**: `audiobook-proxy.service` uses venv Python (`/opt/audiobooks/library/venv/bin/python`) instead of system Python
- **Proxy**: Removed duplicate CORS headers from proxy error responses
- **Mover**: `move-staged-audiobooks` now uses venv Python for `import_single.py`
- **Shell**: Reverted all 35 scripts from `#!/usr/bin/env zsh` back to `#!/bin/bash` — bash is the universal Linux standard, maximizes portability across distros
- **Shell**: Removed all zsh-specific workarounds (reserved variable comments, echo JSON corruption notes, `${0:A:h}` syntax)
- **Shell**: Simplified `audiobook-config.sh` source guard from dual bash/zsh to bash-only
- **Shell**: Converted all zsh syntax to bash equivalents — `${(L)var}` → `${var,,}`, `typeset -A` → `declare -A`, `${(@kv)}` → `${!array[@]}`, `(N)` → `shopt -s nullglob`
- **Upgrade**: Added `audiobook-downloader.timer` and `audiobook-shutdown-saver` to upgrade.sh service stop/start lists
- **Uninstall**: Replaced `arr=($(cmd))` with `mapfile -t arr < <(cmd)` to fix ShellCheck SC2207 word splitting
- **CI**: Added `libsqlcipher-dev` system dependency for Python tests

### Added

- **CI**: ShellCheck linting in GitHub Actions — catches shell script errors at PR time

## [6.6.5.1] - 2026-02-24

### Changed

- **Auth**: Unified all invitation expiry to 48 hours — TOTP/passkey claim tokens now store `claim_expires_at` in `access_requests` table; magic link invitations changed from 24h to 48h
- **Auth**: All invitation email templates updated with correct 48-hour expiry notice
- **Auth**: Added `claim_expires_at` column to `access_requests` table with automatic migration

## [6.6.5] - 2026-02-24

### Added

- **Collections**: "Lectures" collection (Feynman Physics x4, Carol Ann Lloyd x1) with `bypasses_filter=True`

### Changed

- **Library**: Lectures and Great Courses content hidden from main library — only visible through dedicated collections
- **Collections**: Great Courses collection updated with `bypasses_filter=True`
- **Collections**: "Podcasts & Shows" query updated to also exclude Lecture content type

### Fixed

- **Cover Art**: Standalone cover recovery — scanner now finds standalone `.jpg`/`.png` files (extracted by converter) and copies them to `.covers/` cache when no embedded cover art exists in Opus files. Recovers 645 of 646 previously missing covers
- **Converter**: Added `VENV_PYTHON` variable for cover art embedding — `embed_ogg_cover()` now uses the venv Python (which has mutagen) instead of bare `python3`

## [6.6.4] - 2026-02-24

### Added

- **Collections**: "Podcasts & Shows" collection with `bypasses_filter=True` for non-audiobook content types (Podcast, Show, Episode, Radio/TV)
- **Scripts**: `populate_content_types.py` — queries Audible API library + catalog to tag content types

### Changed

- **Converter**: Only processes DRM-encrypted formats (AAXC/AAX/AA); playable formats (MP3, M4A, etc.) are skipped with a message instead of silently ignored

### Fixed

- **Config**: Bash/zsh compatibility in `audiobook-config.sh` — detects `BASH_SOURCE[0]` before falling back to zsh `${0:A:h}`, preventing unbound variable errors in bash scripts with `set -u`
- **Service**: Removed vestigial `/opt/audiobooks/library/data` from `ReadWritePaths` that caused 230+ namespace failures when directory didn't exist under `ProtectSystem=strict`
- **Service**: Added `StartLimitBurst=5`/`StartLimitIntervalSec=60` to prevent rapid restart loops
- **Service**: Changed proxy `Requires` to `PartOf` so proxy restarts with API service
- **Converter**: Fixed queue builder prefix title matching — added word-boundary enforcement so "trial" no longer false-matches "trials of koli"
- **Install**: `ReadWritePaths` patching in `install.sh` and `upgrade.sh` when `AUDIOBOOKS_DATA` differs from `/srv/audiobooks`
- **Cover Art**: Warning when ffmpeg succeeds but cover file not created (ProtectSystem=strict silently blocks writes)

## [6.6.3] - 2026-02-23

### Added

- **Collections**: Restructured from flat list to hierarchical tree with 18 top-level genres and 35 subgenre children (53 navigable items). Categories: special, main, nonfiction, subgenre
- **Collections**: Collapsible sidebar navigation — parent genres have toggle arrows for expand/collapse of subgenre branches
- **Collections**: New genres: Romance, Politics & Social Sciences, Religion & Spirituality, Young Adult, plus subgenres (Police Procedurals, Espionage, Hard-Boiled, Noir, Space Opera, Dystopian, Post-Apocalyptic, etc.)

### Changed

- **UI**: Replaced fixed font-size/padding/gap values with CSS `clamp()` across all 9 CSS files for smooth fluid scaling at any viewport size
- **UI**: Removed redundant breakpoint overrides — breakpoints now only handle structural layout changes (flex-direction, grid columns, element hiding)
- **UI**: Wrapped grid `minmax()` with `min()` to prevent horizontal overflow on narrow screens

### Fixed

- **UI**: Eliminated jarring layout jumps between breakpoints on mobile and tablet viewports
- **Deploy**: Consolidated `deploy.sh` and `deploy-vm.sh` into `upgrade.sh` with `--remote`, `--user`, and `--yes` flags

## [6.6.2.6] - 2026-02-23

### Fixed

- **Deploy**: Fixed all deployment scripts (`deploy.sh`, `install.sh`, `upgrade.sh`) using pyenv shim for venv creation — symlinks into `/home/` are inaccessible under systemd `ProtectHome=yes`, causing API crash-loops. Now explicitly uses system Python (`/usr/bin/python3.14`) with broken-venv and `/home/`-path detection
- **Deploy**: Added `--exclude='venv'` to `deploy-vm.sh` rsync to prevent overwriting production venvs with dev pyenv-based venvs
- **Upgrade**: Added post-upgrade venv health check to `upgrade.sh` — detects broken symlinks and pyenv paths, recreates with system Python and reinstalls dependencies

## [6.6.2.5] - 2026-02-23

### Fixed

- **Collections**: Fixed historical-fiction collection returning 0 results — was using `genre_query('Fiction')` (non-existent genre) instead of `genre_query("Historical Fiction")` (now returns 102 books)
- **Collections**: Fixed action-adventure collection only catching 13 books via text search — switched to `multi_genre_query(["Action & Adventure", "Adventure", "Sea Adventures"])` (now returns 190 books)
- **Collections**: Removed dead `text_search_query()` helper function (no longer used by any collection)
- **Tests**: Updated schema version assertions from 5 to 6 and table count from 16 to 17 in test_auth.py, test_auth_api.py, and test_upgrade_safety.py (reflect migration 006 adding webauthn_credentials table)

## [6.6.2.4] - 2026-02-23

### Fixed

- **Auth**: Added `safeJsonParse()` to all 8 auth HTML pages — gracefully handles HTML error responses (500s, WAF blocks) instead of crashing with `Unexpected token '<'`
- **Auth**: Added missing `webauthn_credentials` table to schema.sql and migration 006 — passkey/security key registration was failing with `no such table` error
- **Auth**: Reordered WebAuthn response handling to check `response.ok` before parsing JSON, preventing parse errors on error responses

## [6.6.2.3] - 2026-02-23

### Fixed

- **Web UI**: Added cache-busting version params (`?v=X.Y.Z`) to all `<script>`, `<link>`, and CSS `@import` references across all 12 HTML files — prevents browsers from serving stale JS/CSS after deploys
- **Web UI**: Fixed user dropdown menu extending beyond left browser edge — changed `right: 0` to `left: 0` in `.user-dropdown` CSS
- **Web UI**: Added null guards to `escapeHtml()`, `selectAuthor()`, and `selectNarrator()` in library.js to prevent "null" text in filter/search inputs

## [6.6.2.2] - 2026-02-22

### Added

- **Uninstall**: Comprehensive `uninstall.sh` with dynamic discovery — finds and removes all traces (27 symlinks, 12 systemd units, configs, certs, runtime files, user/group) with `--keep-data`/`--delete-data`/`--dry-run`/`--force` options
- **Uninstall**: Group membership cleanup before `groupdel` to prevent PAM/SSH failures for other users

### Fixed

- **Install**: zsh reserved variable bugs — `local path=` corrupts `$PATH` (tied variable), `local status=` fails (read-only); renamed to `target_path`/`svc_state` across install.sh, upgrade.sh, migrate-api.sh
- **Install**: `show_detected_storage()` silent abort when directories don't exist yet — added fallback defaults
- **Docs**: Corrected VM snapshot revert procedure (discard overlay, don't commit into base)

## [6.6.2.1] - 2026-02-22

### Added

- **Upgrade**: `--force` flag for `upgrade.sh` to allow same-version reinstall

### Fixed

- **Docs**: Updated `paths-and-separation.md` to reflect actual production layout (`/opt/audiobooks`)

## [6.6.2] - 2026-02-22

### Added

- **Auth**: Magic link UX overhaul for non-technical users — admin invite defaults to magic link, auto-fill claim page from email URL params with auto-submit, inline "Send me a new link" form on expired verify page, improved login magic link sent state
- **UI**: Mobile responsive utilities — horizontal scroll tabs, iOS auto-zoom prevention, small phone (≤480px) and landscape orientation breakpoints

### Changed

- **UI**: Removed Audible Sync tab, section, and all related JS/CSS (replaced by per-user position tracking)
- **UI**: Utilities tabs reduced from 7 to 6 (Database, Conversion, Duplicates, Bulk Ops, Activity, System)
- **Dependencies**: Removed `audible` and `audible-cli` packages from requirements (Audible Sync removed)

### Fixed

- **Auth**: Edit Profile passkey switching — added `novalidate` on form, explicit button types to prevent browser validation errors
- **Auth**: Missing `import json` in auth.py WebAuthn registration handler (F821)
- **Auth**: Claim email URLs now include username and token params for auto-fill
- **UI**: Marquee "NEW" badge showing with no titles — fixed guard to check `data.books.length` instead of `data.count`
- **UI**: Marquee click-anywhere-to-dismiss removed (only dismiss button works now)
- **UI**: Edit Profile modal off-screen on small viewports — added `modal-small` class
- **CI**: Fixed `audible` vs `httpx` version conflict that broke CI tests and pip-audit

## [6.6.1.1] - 2026-02-22

### Added

- **Auth**: Magic link authentication as selectable auth method in claim flow — users choose TOTP, passkey, or magic link during account setup
- **Auth**: Profile auth method switching — users can switch between TOTP, passkey, and magic link from their profile settings
- **UI**: Auth method selector added to utilities.html invite modal (TOTP, Magic Link, Passkey) with contextual hints, defaulting to magic link

### Fixed

- **Build**: `test-report.md` and `audit-*.log` added to `.gitignore` (were being tracked)

## [6.6.1] - 2026-02-22

### Added

- **Security**: HTTP security headers on all API responses: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy` (default-src 'self', media-src 'self' blob:), `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- **Security**: `Strict-Transport-Security` header (HSTS, 1-year, includeSubDomains) when HTTPS is enabled
- **Config**: `AUDIOBOOKS_HTTP_REDIRECT_ENABLED` variable added to `lib/audiobook-config.sh` defaults (default: true)
- **Tests**: `.coveragerc` added with 85% minimum coverage threshold

### Changed

- **CI**: Upgraded Python version in `ci.yml` from 3.11 to 3.14 to match project requirements
- **Security**: Session cookies hardened with `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`

### Fixed

- **Security**: Patched CVE-2025-43859 (h11 HTTP request smuggling) — upgraded h11 to 0.16.0, httpcore to 1.0.9, httpx to 0.28.1
- **Install**: `tmpfiles.conf` source filename corrected in `install.sh` and `upgrade.sh` (was using wrong path pattern, causing `/tmp/audiobook-staging` and `/tmp/audiobook-triggers` to not be recreated on reboot)
- **Security**: `NoNewPrivileges=yes` added to `audiobook-upgrade-helper.service` (was incorrectly set to `no`)
- **Manifest**: `install-manifest.json` updated to version 6.6.1, corrected port 8081 → 8080 for HTTP redirect, corrected `audiobook-mover` expected state from `inactive` to `active`
- **Docker**: `.dockerignore` glob patterns fixed (`__pycache__` → `**/__pycache__`, `*.py[cod]` → `**/*.py[cod]`) to exclude Python bytecode in all subdirectories
- **Tests**: `test_player_features_documented` decoupled from `test_audiobook` fixture (fixture was required but never used by the test body)

## [6.6.0] - 2026-02-22

### Changed

- **Scripts**: Eliminated script drift between repo and production — replaced 6 stale full copies in `/usr/local/bin/` with symlinks to canonical `/opt/audiobooks/scripts/` location
- **Scripts**: Added versioned wrapper scripts to `scripts/` directory (audiobook-api, audiobook-web, audiobook-scan, audiobook-import, audiobook-config, audiobook-user, audiobook-upgrade, audiobook-migrate) replacing inline generation
- **Deploy**: Added `refresh_bin_symlinks()` function and SCRIPT_ALIASES map to deploy.sh, upgrade.sh, install.sh, install-system.sh, and deploy-vm.sh for consistent symlink maintenance
- **Install**: Replaced inline wrapper script generation with shared symlink refresh pattern across all installation entry points

## [6.5.0.1] - 2026-02-22

### Changed

- **CLI Naming**: Standardized all CLI commands from plural `audiobooks-*` to singular `audiobook-*` across install scripts, systemd services, docs, and install-manifest
- **YAML**: Fixed 76 yamllint issues (document-start markers, truthy quoting, indentation, line-length wrapping) across all workflow and config YAML files
- **Markdown**: Fixed ~2,255 markdownlint issues (heading spacing, code block language specifiers, list formatting) across 40+ documentation files
- **Shell**: Applied shfmt formatting to scripts/purge-users.sh

## [6.5.0] - 2026-02-22

### Added

- **Release Workflow**: Two-phase release support (`--local` stage and `--promote` publish) for testing releases before publishing to GitHub

### Changed

- **Systemd**: Added restart limits to proxy service for boot race recovery
- **Systemd**: Added `RequiresMountsFor` data directory mount dependency to prevent boot race 502s
- **CSS**: Improved header flex-wrap and refined marquee neon styling
- **CSS**: Corrected viewport handling for layout consistency

### Fixed

- **Security**: Fixed log injection vulnerability in utilities_crud.py (integer cast sanitization)
- **Dependencies**: Added missing `audible-cli` to requirements.txt
- **Tests**: Backoffice integration tests gracefully skip when Audible is unconfigured
- **Systemd**: Corrected venv path in audiobook-api service file
- **Scripts**: Separation check no longer falsely flags legitimate production symlinks

## [6.4.0.1] - 2026-02-22

### Fixed

- **Scripts**: Separation check in `upgrade.sh` and `install.sh` falsely flagged legitimate production symlinks as dev contamination — `grep "$SCRIPT_DIR"` matched `/opt/audiobooks` paths when run from production; changed to check for `ClaudeCodeProjects` specifically
- **Scripts**: Fixed `install.sh` glob pattern from `audiobooks-*` to `audiobook-*` to match actual symlink names

## [6.4.0] - 2026-02-22

### Added

- **Guest Access**: Unauthenticated visitors can browse the library, search, and view book details without an account
- **Guest Gate**: Play/download buttons show a styled tooltip directing guests to sign in or request access
- **Magic Link Auth**: Email-based authentication as an alternative to TOTP — admin can invite users with magic link auth type
- **Magic Link Login**: Users with magic_link auth type receive sign-in links via email instead of entering TOTP codes
- **Auth Method Preference**: Users can switch between TOTP, passkey, and magic link authentication in their profile
- **Persistent Login**: Multi-layer session persistence (cookie + localStorage + IndexedDB) with "Stay logged in" option
- **Session Restore**: `POST /auth/session/restore` endpoint recovers sessions from client-side storage
- **Auth Status**: `GET /auth/status` public endpoint returns auth state for frontend guest/user detection
- **Upgrade Safety**: Pre-upgrade auth database backup and post-upgrade validation in `upgrade.sh`
- **Schema Migration v4→v5**: Adds `magic_link` auth type, `is_persistent` session flag, `preferred_auth_method` on access requests
- **Purge Script**: `scripts/purge-users.sh` — reusable script to delete users not in a keep list
- **Docker Tests**: 19 comprehensive Docker container tests (build, lifecycle, API, volumes, env, security)
- **Upgrade Safety Tests**: Migration integrity tests verifying tokens, sessions, and credentials survive schema upgrades

### Changed

- **Docker**: Upgraded base image from `python:3.11-slim` to `python:3.14-slim` (Debian Trixie, Python 3.14.3)
- **Docker**: Added `apt-get upgrade -y` and `pip install --upgrade pip` for security patching
- **Docker**: Created `requirements-docker.txt` excluding `audible` package (not needed in standalone container)
- **Auth Endpoints**: Read-only API endpoints (`/api/audiobooks`, `/api/collections`, etc.) now use `@guest_allowed` instead of `@auth_if_enabled`
- **Login UI**: Magic link users see email-based login flow instead of TOTP/passkey forms
- **Admin Invite**: Invite modal includes auth method selector (TOTP, Magic Link, Passkey)

### Fixed

- **Test**: Fixed `test_generate_backup_code_format` — `isupper()` returns `False` for all-digit strings, changed to `part == part.upper()`
- **Docker**: Increased health check timeout for slower build environments
- **Docker**: Fixed entrypoint bind address for container networking

## [6.3.0] - 2026-02-21

### Added

- **Per-User State**: New auth database tables for listening history, download tracking, and user preferences (migration `004_per_user_state.sql`)
- **API**: New `/api/user/history` endpoint — per-user listening history with pagination and date filters
- **API**: New `/api/user/downloads` endpoint — per-user download history with pagination
- **API**: New `/api/user/downloads/<id>/complete` endpoint — record download completion
- **API**: New `/api/user/library` endpoint — personalized library view with progress bars and recently listened
- **API**: New `/api/user/new-books` endpoint — books added since user's last visit
- **API**: New `/api/user/new-books/dismiss` endpoint — mark new books as seen
- **API**: New `/api/admin/activity` endpoint — admin audit log with filtering by user, type, and date range
- **API**: New `/api/admin/activity/stats` endpoint — aggregate activity statistics (listens, downloads, active users, top content)
- **API**: New `/api/genres` endpoint — list all genres with book counts
- **API**: New `PUT /api/audiobooks/<id>/genres` endpoint — set genres for a single audiobook
- **API**: New `POST /api/audiobooks/bulk-genres` endpoint — add/remove genres across multiple audiobooks
- **UI**: My Library tab with progress bars, listening history, and recently-listened section
- **UI**: Art Deco neon new-books marquee highlighting recently added audiobooks
- **UI**: About The Library page with credits, third-party attributions, and dynamic version display
- **UI**: Activity audit section in Back Office with stats cards, top-listened/downloaded lists, filterable activity log, and pagination
- **UI**: Genre management in Back Office Bulk Ops — genre picker with add/remove modes and new genre creation
- **UI**: JavaScript fetch/blob download with completion tracking (replaces raw anchor downloads)
- **Docs**: Help page updated with sections for My Library, progress tracking, downloads, and new books
- **Docs**: Tutorial updated with steps for new per-user features
- **Tests**: Multi-user integration tests and auth-disabled fallback tests
- **Tests**: Per-user state schema and model tests
- **Tests**: About page, activity audit UI, genre management, help update tests

### Changed

- **Position Sync**: Removed Audible cloud sync dependency — position tracking is now fully local and per-user
- **Position Sync**: Positions stored in encrypted auth database (SQLCipher) instead of main library database
- **Docs**: Rewrote `docs/POSITION_SYNC.md` for per-user local-only system
- **Docs**: Updated `docs/ARCHITECTURE.md` with new tables, blueprints, and endpoint documentation

### Fixed

- **UI**: About page version display parsed raw JSON text instead of extracting version field (`r.text()` → `r.json().version`)

## [6.2.0.1] - 2026-02-20

### Fixed

- **UI**: Header title now visually centered using 3-column flex layout (replaced absolute positioning that caused off-center title with asymmetric nav content)

## [6.2.0] - 2026-02-20

### Added

- **Health**: New unauthenticated `/api/system/health` endpoint for monitoring (returns status, version, database connectivity)
- **UI**: Help system with 11-section user guide and interactive 11-step spotlight tutorial
- **Tests**: 50 new tests for health endpoint, proxy headers, help page, tutorial, header layout

### Changed

- **Security**: FLASK_DEBUG default changed from `true` to `false`
- **Security**: USE_WAITRESS default changed from `false` to `true` (production-safe)
- **Security**: Added `Access-Control-Allow-Credentials` header when CORS origin is specific
- **Security**: Added `@admin_or_localhost` decorator to `/api/system/upgrade/check`
- **Security**: Added hop-by-hop header filtering in proxy responses
- **Infrastructure**: systemd service ExecStart wrapper names aligned with installed scripts
- **Infrastructure**: Dockerfile HEALTHCHECK uses `/api/system/health` instead of data endpoint
- **Infrastructure**: HTTP redirect port corrected (8081 → 8080 to match audiobook-config.sh)
- **Quality**: Shell formatting (shfmt) applied to 45 scripts
- **Quality**: Python formatting (ruff format) applied to all backend code
- **Quality**: YAML lint fixes in CI workflows

### Fixed

- **UI**: Back Office button no longer visible to non-admin users (CSS `display:flex` was overriding `hidden` attribute)
- **UI**: Header restructured with balanced left/right navigation
- **Database**: Added `try/finally` to `get_hash_stats` and `get_duplicates` for connection cleanup
- **Paths**: Eliminated remaining hardcoded `/hddRaid1/Audiobooks` paths in duplicates.py, hashing.py, and scripts
- **Docker**: docker-compose.yml image name corrected (`audiobook-toolkit` → `audiobook-manager`)
- **Docker**: Added comprehensive `.dockerignore` entries for dev artifacts
- **Docs**: Added `/api/system/health` to README API table and ARCHITECTURE health checks
- **Docs**: Updated AUTH_RUNBOOK health check script to use `/api/system/health`
- **Branding**: Corrected `greogory` → `TheBoscoClub` in Dockerfile and systemd targets

## [6.1.3] - 2026-02-19

### Fixed

- **Auth**: Rewrite invite flow — invitations no longer pre-create users, eliminating "credentials already claimed" and method selection loop bugs during claim
- **Auth**: TOTP and WebAuthn claim endpoints now read invite metadata for admin-set download permissions
- **Auth**: Delete user now cascade-deletes associated access requests, preventing orphaned records
- **Auth**: Invite endpoint replaces stale access requests instead of blocking with "already exists" error
- **Admin**: Download toggle button now calls correct API endpoint (`/toggle-download` POST instead of non-existent `/permissions` PUT)
- **Scan**: Library rescan progress meter now shows real-time updates in web UI (was stuck at 5% due to ANSI escape codes in scanner output breaking regex parser)

## [6.1.2.1] - 2026-02-18

### Added

- **Admin**: Invite User button in user administration page for pre-registering and approving new users with claim token workflow

## [6.1.2] - 2026-02-18

### Fixed

- **Auth**: First-user registration returned backup codes as formatted string instead of JSON array, causing JavaScript TypeError displayed as "Connection error"
- **Auth**: Added clipboard copy button for TOTP backup codes on registration page
- **Proxy**: HTTP error handler now forwards Flask's original response body instead of generic error message
- **Upgrade**: Removed data directories (`/srv/audiobooks`, `/hddRaid1/Audiobooks`) from installed app detection candidates — only actual app installation paths are checked
- **System**: Removed development-specific paths from project discovery endpoint, keeping only `AUDIOBOOKS_PROJECT_DIR` env var and generic fallbacks

## [6.1.1] - 2026-02-18

### Fixed

- **Scripts**: Comprehensive bash-to-zsh compatibility fixes across all shell scripts
  - Convert `read -p` bash-isms to zsh `read "?prompt"` syntax
  - Convert `${var,,}` bash lowercase to zsh `${(L)var}` syntax
  - Fix associative array iteration, string manipulation, and other bash-specific patterns
- **CI**: Track `library/auth/schema.sql` in git (was excluded by `*.sql` gitignore rule, breaking CI auth tests)
- **CI**: Add `# noqa: E402` to test files with `sys.path.insert()` before imports (fixes ruff linting in CI)

## [6.1.0] - 2026-02-18

### Added

- **UI**: Comprehensive responsive design for mobile, desktop, portrait, landscape, and zoom/pinch scenarios
  - New `responsive.css` (425 lines, 6 media queries) with safe area insets, touch-aware interactions, landscape compaction, tablet/small phone layouts, fluid scaling, and reduced motion support
  - `viewport-fit=cover` on all HTML pages for notched device support
  - Touch targets minimum 44px (Apple HIG), `touch-action: manipulation` to eliminate 300ms tap delay
  - `@media (prefers-reduced-motion: reduce)` accessibility support
  - `clamp()` fluid typography and spacing for smooth desktop resize

### Changed

- **UI**: Header navigation converts to flex column layout at 768px breakpoint (fixes overlap with title)
- **UI**: Audio player compacts in landscape mobile orientation (max-height: 500px)
- **CI**: GitHub Actions release workflow installs zsh on runner for script compatibility
- **CI**: Fixed GHCR package permissions for Docker image push

### Fixed

- **Install**: `install.sh` separation check uses dynamic `$SCRIPT_DIR` instead of hardcoded path pattern
- **Install**: `upgrade.sh` separation check uses dynamic `$SCRIPT_DIR` instead of hardcoded path pattern
- **Code**: Removed unused `PilImage` import from `library/auth/totp.py`

## [6.0.0] - 2026-02-18

### Added

- **Security**: Dual-mode security architecture — `admin_or_localhost` decorator adapts endpoint protection based on deployment mode
  - `AUTH_ENABLED=true` (remote): Admin endpoints require authenticated admin user
  - `AUTH_ENABLED=false` (standalone): Admin endpoints restricted to localhost only
  - Admin endpoints are **never** wide-open regardless of mode
- **Install**: System installer (`install-system.sh`) now creates dedicated `audiobooks` service account (group + user with nologin shell)
- **Install**: Auth encryption key auto-generated during system install (64 hex chars, mode 0600, owned by audiobooks user)
- **Install**: Database auto-initialized from `schema.sql` during all install modes (system, user, unified)
- **Install**: Python virtual environment validated functionally (`python --version`) — detects broken symlinks from rsync copies
- **Install**: Python dependencies installed from `requirements.txt` during install (not just Flask)
- **Install**: systemd services configured with `User=audiobooks`, `Group=audiobooks`, `WorkingDirectory`
- **Config**: Remote access configuration variables added to `audiobooks.conf.example`: `AUDIOBOOKS_HOSTNAME`, `BASE_URL`, `CORS_ORIGIN`, `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ORIGIN`
- **Config**: Email/SMTP configuration section added: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `ADMIN_EMAIL`
- **Testing**: `vm-test-manifest.json` added for `/test` Phase V integration
- **Rules**: Project-specific `.claude/rules/` files: `audio-metadata.md`, `paths-and-separation.md`, `testing.md`

### Changed

- **BREAKING**: All 27 shell scripts converted from `#!/bin/bash` to `#!/usr/bin/env zsh` (reverted back to bash in 6.6.5.1)
- **Security**: 9 admin endpoints in `utilities_system.py` now use `@admin_or_localhost` instead of `@localhost_only`
- **API**: CORS origin defaults to `*` (permissive, safe for standalone) — configurable via `CORS_ORIGIN` env var for remote deployments
- **API**: `BASE_URL` auto-detected from request headers — no hardcoded domain defaults
- **API**: Email configuration (`_get_email_config()`) uses agnostic defaults — no hardcoded domain references
- **Proxy**: `proxy_server.py` forwards `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Real-IP`, and `Host` headers from upstream reverse proxies
- **Proxy**: CORS locked to configurable `CORS_ORIGIN` value in proxy responses
- **Config**: `audiobooks.conf.example` reorganized with Remote Access and Email/SMTP sections
- **CI**: Removed `.github/workflows/ci.yml` (was using Python 3.11, incompatible with current Python 3.14 stack)

### Fixed

- **Install**: Wrapper scripts reference `api_server.py` (not stale `api.py`)
- **Install**: Auth key generated as 64 hex chars (`xxd -p | tr -d '\n'`), matching code validation — was base64 (~44 chars)
- **Install**: Auth key permissions set to `audiobooks:audiobooks 0600` — was `root:audiobooks 0640`
- **Install**: Correct pip package name `webauthn` (not `py-webauthn`)
- **Testing**: Stale VM name `test-vm-cachyos` → `test-audiobook-cachyos` in pytest.ini and integration test docstrings
- **Deps**: `pillow` 12.1.0 → 12.1.1 (GHSA-cfh3-3jmp-rvhc, OOB write on PSD)
- **Deps**: `cryptography` floor raised to ≥46.0.5 (GHSA-r6ph-v2qm-q3c2, subgroup attack)

## [5.0.2] - 2026-02-06

### Added

- **Testing**: VM_TESTS environment variable for proper WebAuthn origin selection in integration tests
- **JS**: Optional onCancel callback for showConfirmModal to support async confirm dialogs

### Changed

- **Testing**: Update test VM from test-vm-cachyos to test-audiobook-cachyos (192.168.122.104)
- **Deploy**: Add library/scripts/ and library/common.py to VM deployment sync

### Fixed

- **API**: Use sys.executable instead of hardcoded "python3" in subprocess calls for venv compatibility
- **API**: Prevent duplicate access request errors with has_any_request() check
- **Scripts**: Initialize bash array to avoid unbound variable error with set -u
- **Scripts**: Fix shellcheck warnings in download-new-audiobooks (SC2188, SC2038, SC2086)
- **Deploy**: Correct venv path from /opt/audiobooks/library/venv to /opt/audiobooks/venv
- **Deploy**: Add /opt/audiobooks/library/data to systemd ReadWritePaths
- **Tests**: Fix WebAuthn origin mismatch for VM tests (port 8443 vs 9090)
- **Tests**: Fix SSH cleanup command venv path in auth integration tests

### Security

- **CI**: Add explicit permissions blocks to all GitHub Actions workflow jobs

## [5.0.1.1] - 2026-02-01

### Removed

- **Periodicals**: Remove all remaining periodicals code, systemd services, sync scripts, and install manifest entries (feature was removed in v4.0.3 but artifacts remained)
- **Periodicals**: Clean up "periodicals" and "Reading Room" references in code comments across audiobooks.py, schema.sql, metadata_utils.py, populate_asins.py

### Fixed

- **Systemd**: Fix API service boot failures caused by ProtectSystem=strict resolving `/hddRaid1` symlink to unmounted `/hddRaid1/Audiobooks` — use real mount path and explicit After=hddRaid1-Audiobooks.mount ordering
- **Systemd**: Fix HTTPS proxy permanently failing on boot due to cascade dependency failure from API service
- **Systemd**: Fix stale symlinks with wrong "audiobooks-" prefix (should be "audiobook-") for shutdown-saver and upgrade-helper units
- **Systemd**: Update ExecStartPre port checks from lsof to ss (iproute2, always available)

## [5.0.1] - 2026-01-30

### Fixed

- **Proxy**: HTTPS reverse proxy now routes `/auth/*` endpoints to Flask backend (was only proxying `/api/*` and `/covers/*`, causing auth endpoints to return 405)
- **Proxy**: Forward `Cookie` header through reverse proxy for session-based authentication
- **Docs**: Updated all project documentation for v5.0.0 authentication release

## [5.0.0] - 2026-01-29

### Added

- **Authentication**: Multi-user authentication system with three auth methods:
  - **TOTP** (authenticator app) - time-based one-time passwords via Authy, Google Authenticator, etc.
  - **Passkey** (platform authenticator) - biometrics, phone, password manager (Bitwarden, 1Password)
  - **FIDO2** (hardware security key) - YubiKey, Titan Security Key, etc.
- **Authentication**: Encrypted auth database using SQLCipher (AES-256 at rest)
- **Authentication**: Admin approval flow for new user registrations with claim token system
- **Authentication**: Backup code recovery (8 single-use codes per user)
- **Authentication**: Session management with secure HTTP-only cookies
- **Authentication**: Per-user playback position tracking
- **Authentication**: WebAuthn/FIDO2 with dynamic origin detection from deployment config
- **Web UI**: Login page with auth-method-aware form (TOTP code input vs WebAuthn tap prompt)
- **Web UI**: Claim page for new users to set up credentials after admin approval
- **Web UI**: Admin panel for user management (approve/deny requests, edit users, view sessions)
- **Web UI**: Contact page and notification system
- **API**: Auth-gated endpoints with conditional decorators (bypass when AUTH_ENABLED=false)
- **API**: Download endpoint for offline audiobook listening
- **Server**: HTTPS reverse proxy with TLS 1.2+ and HTTP-to-HTTPS redirect
- **Infrastructure**: VM deployment script for remote testing
- **Infrastructure**: Caddy-based development server configuration

### Changed

- **BREAKING**: All API endpoints now require authentication when AUTH_ENABLED=true
- **BREAKING**: Web UI redirects to login page for unauthenticated users
- Passkey registration no longer restricts to platform authenticators (allows phone, password manager, hardware key)
- WebAuthn origin and RP ID auto-derived from AUDIOBOOKS_HOSTNAME, WEB_PORT, and HTTPS settings
- Token generation uses alphanumeric-only alphabet to avoid dash ambiguity in formatted tokens

### Fixed

- WebAuthn registration parsing uses py-webauthn 2.7.0 helper functions (not Pydantic model methods)
- NoneType.strip() crash on nullable recovery_email/recovery_phone fields
- WebAuthn JS API paths corrected from /api/auth/ to /auth/
- Backup codes returned as array (not formatted ASCII string) for frontend .forEach() compatibility
- WebAuthn claim flow creates session for auto-login (matching TOTP behavior)
- Hostname detection treats .localdomain and single-label hostnames as localhost for RP ID

## [4.1.2] - 2026-01-22

### Added

- **Web UI**: "Check for Updates" button in Utilities page for dry-run upgrade preview
  - Shows verbose output of what would happen without making changes
  - Displays current vs available version comparison
  - Reports result of multi-installation detection

### Fixed

- **Upgrade**: Fixed `--from-github` and `--from-project` options not upgrading the correct installation
  - `find_installed_dir()` now prioritizes system paths (`/opt/audiobooks`) over custom data locations
  - Adds warning when multiple installations are found, showing versions of each
  - Tells user to use `--target` if auto-selected location isn't correct

## [4.1.1] - 2026-01-20

### Fixed

- **Security**: Fixed insecure temporary file creation in ASIN population subprocess (CodeQL alert #187)
  - Changed `tempfile.mktemp()` to `tempfile.mkstemp()` in `maintenance.py`
  - Prevents TOCTOU (time-of-check-time-of-use) race condition vulnerability
  - The atomically-created file descriptor is immediately closed so the subprocess can write to it

## [4.1.0] - 2026-01-20

### Added

- **Player**: Media Session API integration for OS-level media controls:
  - Lock screen playback controls (play/pause, seek forward/back, skip)
  - Notification center media controls
  - Track metadata display (title, author, narrator, cover art)
  - Progress bar with seek support
- **Player**: Live Audible position sync during local playback:
  - Automatically syncs position with Audible every 5 minutes while playing
  - Uses "furthest ahead wins" logic to preserve furthest progress
  - Graceful handling when Audible service is unavailable
  - Only syncs books with ASIN (Audible-sourced audiobooks)

## [4.0.5] - 2026-01-20

### Fixed

- **Security**: Addressed 26 CodeQL alerts with TLS hardening and documentation:
  - Enforce TLS 1.2 minimum version in HTTPS server (was allowing older versions)
  - Replace stack trace exposure with generic error message in bulk delete API
  - Added CodeQL suppression comments for validated false positives (SQL injection with allowlists, path injection with validation, SSRF with localhost-only access, XSS with escapeHtml sanitization)

## [4.0.4] - 2026-01-20

### Fixed

- **Systemd**: Fixed API service failing at boot with NAMESPACE error on HDD/NAS storage. Added `/hddRaid1/Audiobooks` to `RequiresMountsFor` so systemd waits for the data mount before setting up the security namespace. Previously only waited for `/opt/audiobooks`.
- **Auth**: Fixed timestamp format mismatch in session cleanup causing incorrect stale session deletion. SQLite uses space separator (`YYYY-MM-DD HH:MM:SS`) while Python's `isoformat()` uses `T` separator, causing string comparison failures.

### Added

- **Documentation**: Added "HDD and Network Storage Considerations" section to README explaining how to configure `RequiresMountsFor` for slow mounts (HDDs, NAS, NFS, CIFS)

## [4.0.3] - 2026-01-18

### Fixed

- **API**: All async operations (Audible download, library import, rescan) now stream real-time progress with detailed item counts, percentages, and status updates
- **Docker**: Synced Dockerfile `ARG APP_VERSION` default to match VERSION file (4.0.2 → 4.0.3)
- **Code Quality**: Removed unused imports and marked unused regex patterns in test and library code

## [4.0.2] - 2026-01-18

### Fixed

- **API**: Fixed library rescan progress reporting to properly capture scanner output. Scanner uses carriage returns (`\r`) for in-place progress updates, but the API was only reading newline-terminated lines. Now reads character-by-character to capture both `\r` and `\n` delimited output.
- **Scripts**: Fixed duplicate entries in `source_checksums.idx`. The `generate_source_checksum()` function now checks if a filepath already exists before appending, preventing the same file from being indexed multiple times.
- **Systemd**: Fixed "Read-only file system" error when rebuilding conversion queue. Added `AUDIOBOOKS_DATA` path (`/hddRaid1/Audiobooks`) to `ReadWritePaths` in `audiobook-api.service` since `ProtectSystem=strict` was blocking write access to the index directory.

## [4.0.1] - 2026-01-17

### Fixed

- **API**: Library rescan now streams real-time progress updates to the web UI. Previously showed "Starting scanner..." for the entire scan duration; now shows actual progress with file counts and percentages.
- **Security**: Patched CVE-2025-43859 (h11 HTTP request smuggling) by upgrading to h11 0.16.0
- **Security**: Patched CVE-2026-23490 (pyasn1 parsing issue) by upgrading to pyasn1 0.6.2
- **Security**: Added CodeQL suppression comments for validated false positives in path handling and log sanitization code

## [4.0.0.2] - 2026-01-17

### Fixed

- **CI**: Fixed Docker workflow to support 4-digit tweak versions (X.Y.Z.W). The `docker/metadata-action` semver pattern doesn't handle 4-segment versions, so switched to raw tag extraction.

## [4.0.0.1] - 2026-01-17

### Fixed

- **Documentation**: Corrected migration path in CHANGELOG.md - was `migrations/010_drop_periodicals.sql`, now correctly shows `library/backend/migrations/010_drop_periodicals.sql`

## [4.0.0] - 2026-01-17

### Removed

- **BREAKING: Periodicals Feature Extracted**: The entire "Reading Room" periodicals subsystem has been removed from the main codebase
  - Removed `library/backend/api_modular/periodicals.py` - Flask Blueprint (~1,345 lines)
  - Removed `library/tests/test_periodicals.py` - Test suite (~1,231 lines)
  - Removed `library/web-v2/periodicals.html` - Reading Room UI (~1,079 lines)
  - Removed `library/web-v2/css/periodicals.css` - CSS module (~1,405 lines)
  - Removed `systemd/audiobook-periodicals-sync.service` - Systemd service
  - Removed `systemd/audiobook-periodicals-sync.timer` - Systemd timer
  - Removed `scripts/sync-periodicals-index` - Sync script (~391 lines)
  - Removed `docs/PERIODICALS.md` - Feature documentation
  - Total: ~5,700 lines removed

### Changed

- **Database Migration**: Added `010_drop_periodicals.sql` to clean up periodicals tables
  - Drops `periodicals`, `periodicals_sync_status`, `periodicals_playback_history` tables
  - Drops related views and triggers
  - Note: `content_type` column in `audiobooks` table is retained
- **Download Script**: Removed podcast episode detection logic from `download-new-audiobooks`
- **Status Script**: Removed periodicals timer from `audiobook-status` service checks
- **Web UI**: Removed "Reading Room" navigation link from main library header
- **Documentation**: Updated README.md and ARCHITECTURE.md to remove periodicals references

### Migration Notes

- **Before upgrading**: Disable periodicals services

  ```bash
  sudo systemctl stop audiobook-periodicals-sync.timer
  sudo systemctl disable audiobook-periodicals-sync.timer
  ```

- **After upgrading**: Run the cleanup migration

  ```bash
  sqlite3 /path/to/audiobooks.db < /opt/audiobooks/library/backend/migrations/010_drop_periodicals.sql
  ```

- **To restore periodicals**: Use tag `v3.11.2-with-periodicals` or branch `feature/periodicals-rnd`

## [3.11.2] - 2026-01-17

### Added

- **Podcast Episode Download & Conversion**: Full support for downloading and converting podcast episodes from Audible
  - `download-new-audiobooks`: Detects podcast episodes via database, uses `--resolve-podcasts` flag for proper MP3 download
  - `convert-audiobooks-opus-parallel`: Handles MP3-to-Opus conversion for podcasts (no DRM, simple ffmpeg transcode)
  - `build-conversion-queue`: Now includes `.mp3` files in source/converted indexing
- **Periodicals Orphan Detection**: Find and delete episodes whose parent series no longer exists
  - `GET /api/v1/periodicals/orphans`: List orphaned episodes
  - `DELETE /api/v1/periodicals/orphans`: Expunge all orphaned episodes (files + database)
  - UI button "🔍 Find Orphans" in periodicals header with modal display

### Fixed

- **Periodicals SSE**: Fixed Flask request context issue in SSE generator by capturing `g.db_path` before generator starts
- **Security - SQL Injection**: Added table name whitelist (`ALLOWED_LOOKUP_TABLES`) in scanner modules to prevent SQL injection via genre/era/topic lookups
- **Security - Log Injection**: Converted 4 files to use `%s` formatting instead of f-strings in log calls (`periodicals.py`, `add_new_audiobooks.py`, `position_sync.py`, `import_single.py`)
- **Security - XSS**: Changed `innerHTML` to `textContent` for user-controlled content in `library.js`
- **Build Queue**: Fixed `build-conversion-queue` to only process AAX/AAXC files, not MP3 podcasts (which don't need DRM removal)
- **Lint**: Added missing `# noqa: E402` comment for module-level import in `test_metadata_consistency.py`

## [3.11.1] - 2026-01-14

### Fixed

- **Deploy Script**: Fixed `deploy.sh` to include root-level management scripts (`upgrade.sh`, `migrate-api.sh`) that were being silently skipped during deployment. These scripts live in the project root but need to be copied to `$target/scripts/` for the `audiobook-upgrade` wrapper to function.

## [3.11.0] - 2026-01-14

### Added

- **Periodicals Sorting**: Reading Room now supports multiple sort options:
  - By title (A-Z, Z-A)
  - By release date (newest/oldest first)
  - By subscription status (subscribed first)
  - By download status (downloaded first)
- **Whispersync Position Sync**: Periodicals now support Audible position synchronization
  - Individual episode sync via `/api/periodicals/<asin>/sync-position`
  - Batch sync for all episodes via `/api/periodicals/sync-all-positions`
  - Real-time progress via SSE endpoint
- **Auto-Download for Subscribed Podcasts**: Automatically queue downloads for new episodes of subscribed series
- **Podcast Expungement**: Complete removal of unsubscribed podcast content including:
  - Audio files, covers, chapter data
  - Database entries with cascade to episodes
  - Index file cleanup
- **ASIN Sync**: Periodicals table now syncs `is_downloaded` status when audiobooks are imported

### Changed

- **Database Path Handling**: Clarified and fixed database path configuration across the codebase
- **Index Rebuilds**: Prevented destructive index rebuilds, added database sync protection

### Fixed

- **Test Schema**: Made periodicals sync conditional to prevent test failures
- **Duplicates Test**: Fixed path validation assertion for out-of-bounds paths
- **SSE Headers**: Removed hop-by-hop `Connection` header for PEP 3333 compliance
- **API Test Expectations**: Added 503 status for unavailable Audible, 400 for missing VERSION
- **Unused Code**: Removed unused `EXPUNGEABLE_TYPES` variable
- **CodeQL Alerts**: Resolved security and lint issues from static analysis

## [3.10.1] - 2026-01-14

### Added

- **Architecture Documentation**: Comprehensive update to ARCHITECTURE.md with 4 new sections:
  - Scanner Module Architecture (data pipeline flow diagram)
  - API Module Architecture (utilities_ops submodules documentation)
  - Systemd Services Reference (complete service inventory)
  - Scripts Reference (21 scripts organized by category)

### Changed

- **Periodicals Sync**: Enhanced parent/child hierarchy support for podcast episodes
  - Sync script now properly tracks episode parent ASINs
  - Improved episode metadata extraction from Audible API

### Fixed

- **Hardcoded Paths**: Fixed 2 hardcoded paths in shell scripts:
  - `move-staged-audiobooks`: Changed `/opt/audiobooks/library/scanner/import_single.py` to `${AUDIOBOOKS_HOME}/...`
  - `sync-periodicals-index`: Changed `/opt/audiobooks/library/backend/migrations/006_periodicals.sql` to `${AUDIOBOOKS_HOME}/...`
- **Systemd Inline Comments**: Removed invalid inline comments from 6 systemd service files (systemd doesn't support inline comments)
- **Test Config**: Updated hardcoded path tests to properly handle systemd files and shell variable defaults

## [3.10.0] - 2026-01-14

### Changed

- **BREAKING: Naming Convention Standardization**: All service names, CLI commands, and config files
  now use singular "audiobook-" prefix instead of plural "audiobooks-" to align with project name
  "audiobook-manager"
  - Renamed `lib/audiobooks-config.sh` → `lib/audiobook-config.sh`
  - Renamed all systemd units: `audiobooks-*` → `audiobook-*`
  - Updated all script references to new config file name
- **Status Script Enhancement**: `audiobook-status` now displays services and timers in separate sections

### Fixed

- **Unused Imports**: Removed 45 unused imports across codebase via ruff auto-fix
- **Test Schema Handling**: Marked schema-dependent tests as xfail pending migration 007
  (source_asin column, content_type column, indexes, FTS triggers)
- **Documentation Dates**: Updated last-modified dates in ARCHITECTURE.md and POSITION_SYNC.md

### Migration Notes

After upgrading, run these commands to migrate systemd services:

```bash
# Stop old services
sudo systemctl stop audiobooks-api audiobooks-converter audiobooks-mover audiobooks-proxy audiobooks-redirect

# Disable old services
sudo systemctl disable audiobooks-api audiobooks-converter audiobooks-mover audiobooks-proxy audiobooks-redirect

# Remove old service files
sudo rm /etc/systemd/system/audiobooks-*.service /etc/systemd/system/audiobooks-*.timer /etc/systemd/system/audiobooks.target

# Run upgrade script
sudo /opt/audiobooks/upgrade.sh
```

## [3.9.8] - 2026-01-14

### Changed

- **Major Refactoring**: Split monolithic `utilities_ops.py` (994 lines) into modular package
  - `utilities_ops/audible.py` - Audible API operations (download, metadata sync)
  - `utilities_ops/hashing.py` - Hash generation operations
  - `utilities_ops/library.py` - Library content management
  - `utilities_ops/maintenance.py` - Database and index maintenance
  - `utilities_ops/status.py` - Status endpoint operations
- **Shared Utilities**: Extract common code to `library/common.py` (replacing `library/utils.py`)
- **Test Coverage**: Added 27 new test files, coverage increased from 77% to 85%
  - New test files for all API modules (audiobooks, duplicates, supplements, position_sync)
  - New test files for utilities_ops submodules
  - Extended test coverage for edge cases and error handling

### Fixed

- **Unused Imports**: Removed `TextIO` from utilities_conversion.py, `Path` from utilities_ops/library.py
- **Incorrect Default**: Fixed AUDIOBOOKS_DATA default in audible.py from `/var/lib/audiobooks` to `/srv/audiobooks`
- **Example Config**: Added missing PARALLEL_JOBS, DATA_DIR, and INDEX variables to audiobooks.conf.example
- **Documentation**: Updated api_modular/README.md to remove obsolete utilities_ops.py references

### Security

- **CVE-2025-43859 Documentation**: Documented h11 vulnerability as blocked by audible 0.8.2 dependency chain
  (audible pins httpx<0.24.0 which requires h11<0.15). Monitor for audible updates.

## [3.9.7.1] - 2026-01-13

### Fixed (Audit Fixes)

- **PIL Rebuild for Python 3.14**: Rebuilt Pillow wheel in virtual environment to fix compatibility
  with Python 3.14 (CachyOS rolling release). PIL was compiled against older Python, causing
  import failures during audiobook cover processing.
- **flask-cors Removal**: Removed deprecated flask-cors from `install.sh` and `install-user.sh`.
  CORS has been handled natively since v3.2.0; the pip install was a no-op that could fail on
  systems without the package available.
- **systemd ConditionPathExists**: Fixed incorrect `ConditionPathExists` paths in multiple
  systemd service files that referenced non-existent queue/trigger files, causing services
  to skip activation silently.

## [3.9.7] - 2026-01-13

### Fixed

- **Upgrade Script Path Bug**: Fixed `upgrade-helper-process` referencing wrong path
  - Was: `/opt/audiobooks/upgrade.sh` (root level, doesn't exist)
  - Now: `/opt/audiobooks/scripts/upgrade.sh` (correct location)
  - This broke the web UI upgrade button and `audiobook-upgrade` command
- **Duplicate Finder Endpoint**: Fixed JavaScript calling non-existent API endpoint
  - Was: `/api/duplicates/by-hash` (doesn't exist)
  - Now: `/api/duplicates` (correct endpoint)
  - This silently broke "Find Duplicates" for hash-based detection in Back Office
- **Upgrade Script Sync**: Added root-level management scripts to `do_upgrade()` sync
  - `upgrade.sh` and `migrate-api.sh` now properly sync from project root to `target/scripts/`
  - Previously these were only installed by `install.sh`, not synced during upgrades

## [3.9.6] - 2026-01-13

### Security

- **CVE-2025-43859**: Fix HTTP request smuggling vulnerability by upgrading h11 to >=0.16.0
- **TLS 1.2 Minimum**: Enforce TLS 1.2 as minimum protocol version in proxy_server.py
  - Prevents downgrade attacks to SSLv3, TLS 1.0, or TLS 1.1
- **SSRF Protection**: Add path validation in proxy_server.py to prevent SSRF attacks
  - Only allows `/api/` and `/covers/` paths to be proxied
  - Blocks attempts to access internal services via crafted URLs
- **Stack Trace Exposure**: Replace 12 instances of raw exception messages in API responses
  with generic error messages; full tracebacks now logged server-side only

### Fixed

- **CodeQL Remediation**: Fix 30 code scanning alerts across the codebase
  - Add missing `from typing import Any` import in duplicates.py
  - Fix import order in utilities_ops.py (E402)
  - Document 7 intentional empty exception handlers
  - Fix mixed return statements in generate_hashes.py
  - Remove unused variable in audiobooks.py
  - Add `__all__` exports in scan_audiobooks.py for re-exported symbols
- **Index Corruption Bug**: Fixed `generate_library_checksum()` in `move-staged-audiobooks`
  that caused phantom duplicates in the library checksum index
  - Bug: Script appended entries without checking if filepath already existed
  - Result: Same file could appear 8+ times in index after reprocessing
  - Fix: Now removes existing entry before appending (idempotent operation)

### Changed

- Upgrade httpx to 0.28.1 and httpcore to 1.0.9 (required for h11 CVE fix)

## [3.9.5.1] - 2026-01-13

### Added

- Multi-segment version badges in README with hierarchical color scheme
- Version history table showing release progression

## [3.9.5] - (Previous)

### Fixed (rolled back from 3.9.7)

- **CRITICAL: Parallelism Restored**: Fixed 7 variable expansion bugs in `build-conversion-queue`
  that completely broke parallel conversions (was running 1 at a time instead of 12)
  - Bug: `: > "queue_file"` (literal string) instead of `: > "$queue_file"` (variable)
  - Introduced by incomplete shellcheck SC2188 fix in fd686b9
  - Affected functions: `build_converted_asin_index`, `build_source_asin_index`,
    `build_converted_index`, `load_checksum_duplicates`, `build_queue`
- **Progress Tracking**: Fixed conversion progress showing 0% for all jobs
  - Changed from `read_bytes` to `rchar` in `/proc/PID/io` parsing
  - `read_bytes` only counts actual disk I/O; `rchar` includes cached reads
  - FFmpeg typically reads from kernel cache, so `read_bytes` was always 0
- **UI Safety**: Removed `audiobook-api` and `audiobook-proxy` from web UI service controls
  - These are core infrastructure services that should not be stoppable via UI
  - Prevents accidental self-destruction of the running application

## [3.9.7] - 2026-01-11 *(rolled back)*

> **Note**: This release was rolled back due to critical bugs in the queue builder
> that broke parallel conversions. The fixes below are valid but were released
> alongside unfixed bugs from 3.9.6. See [Unreleased] for the complete fixes.

### Fixed

- **Database Connection Leaks**: Fixed 6 connection leaks in `position_sync.py`
  - All API endpoints now properly close database connections via try/finally blocks
  - Affected routes: `get_position`, `update_position`, `sync_position`, `sync_all_positions`, `list_syncable`, `get_position_history`
- **Version Sync**: Synchronized version across all files (Dockerfile, install-manifest.json, documentation)
- **Database Path**: Corrected database path in install-manifest.json and documentation
  - Changed from `/var/lib/audiobooks/audiobooks.db` to `/var/lib/audiobooks/db/audiobooks.db`

### Changed

- **Code Cleanup**: Removed unused `Any` import from `duplicates.py`

## [3.9.6] - 2026-01-10 *(never released)*

> **Note**: This version was committed but never tagged/released. The queue script
> fix below was incomplete (claimed 3 instances, actually 7). See [Unreleased] for
> the complete fix.

### Added

- **Storage Tier Detection**: Installer now automatically detects NVMe, SSD, and HDD storage
  - Displays detected storage tier for each installation path
  - Warns if database would be placed on slow storage (HDD)
  - Explains performance impact: "SQLite query times: NVMe ~0.002s vs HDD ~0.2s (100x difference)"
  - Option to cancel installation and adjust paths
- **Installed App Documentation**: New documentation at `/opt/audiobooks/`
  - `README.md` - Quick start guide and service overview
  - `CHANGELOG.md` - Version history for installed application
  - `USAGE.md` - Comprehensive usage guide with troubleshooting

### Fixed

- **Proxy hop-by-hop headers**: Fixed `AssertionError: Connection is a "hop-by-hop" header` from Waitress
  - Added `HOP_BY_HOP_HEADERS` filter to `proxy_server.py` (PEP 3333 / RFC 2616 compliance)
  - Prevents silently dropped API responses through reverse proxy
- **Service permissions**: Fixed silent download failures due to directory ownership mismatch
  - Documented in ARCHITECTURE.md with detection script
- **Rebuild queue script** *(incomplete)*: Attempted fix for variable expansion in `build-conversion-queue`
  - Fixed 3 of 7 instances; remaining 4 caused parallelism to fail

### Changed

- **ARCHITECTURE.md**: Added reverse proxy architecture and service permissions sections
- **INSTALL.md**: Added storage tier detection documentation with example output

## [3.9.5] - 2026-01-10

### Added

- **Schema Tracking**: `schema.sql` now tracked in git repository
  - Contains authoritative database schema with all columns, indices, and views
  - Includes `content_type` and `source_asin` columns for periodical classification
  - Added `library_audiobooks` view and `idx_audiobooks_content_type` index
- **Utility Script**: `rnd/update_content_types.py` for syncing content_type from Audible API
  - Fetches content_type for all library items with ASINs
  - Handles Audible's pagination and inconsistent tagging

### Changed

- **Content Filter**: Expanded `AUDIOBOOK_FILTER` to include more content types
  - Now includes: Product, Lecture, Performance, Speech (main library)
  - Excludes: Podcast, Radio/TV Program (Reading Room)
  - Handles NULL content_type for legacy entries

### Fixed

- **Reliability**: Prevent concurrent `build-conversion-queue` processes with flock
  - Multiple simultaneous rebuilds caused race conditions and duplicate conversions
- **Scripts**: Fixed shellcheck warnings in `build-conversion-queue` and `move-staged-audiobooks`
  - SC2188: Use `: >` instead of `>` for file truncation
  - SC2086: Quote numeric variables properly

## [3.9.4] - 2026-01-09

### Added

- **Developer Safeguards**: Pre-commit hook blocks hardcoded paths in scripts and services
  - Rejects commits containing literal paths like `/run/audiobooks`, `/var/lib/audiobooks`, `/srv/audiobooks`
  - Enforces use of configuration variables (`$AUDIOBOOKS_RUN_DIR`, `$AUDIOBOOKS_VAR_DIR`, etc.)
  - Shareable hooks in `scripts/hooks/` with installer script (`scripts/install-hooks.sh`)
- **Database Schema**: Added `content_type` column to audiobooks table
  - Stores Audible content classification (Product, Podcast, Lecture, Performance, Speech, Radio/TV Program)
  - Added `library_audiobooks` view to separate main library from periodicals
  - New index `idx_audiobooks_content_type` for efficient filtering
  - Used by `AUDIOBOOK_FILTER` to exclude periodical content from main library queries

### Changed

- **Runtime Directory**: Changed `AUDIOBOOKS_RUN_DIR` from `/run/audiobooks` to `/var/lib/audiobooks/.run`
  - Fixes namespace isolation issues with systemd's `ProtectSystem=strict` security hardening
  - Using `/run/` directories doesn't work reliably with sandboxed services

### Fixed

- **Security**: Replace insecure `mktemp()` with `mkstemp()` in `google_play_processor.py`
  - Eliminates TOCTOU (time-of-check-time-of-use) race condition vulnerability
- **Reliability**: Add signal trap to converter script for clean FFmpeg shutdown
  - Prevents orphan FFmpeg processes on service stop/restart
- **Code Quality**: Fix missing `import os` in `librivox_downloader.py`
- **Code Quality**: Remove unused `LOG_DIR` variable from `librivox_downloader.py`
- **Code Quality**: Remove unused `PROJECT_DIR` import from `scan_supplements.py`
- **Code Quality**: Add logging for silent exceptions in `duplicates.py` index updates
- **Systemd Services**: Removed `RuntimeDirectory=audiobooks` from all services
  - API, converter, downloader, mover, and periodicals-sync services updated
  - tmpfiles.d now creates `/var/lib/audiobooks/.run` at boot
- **Periodicals Sync**: Fixed SSE FIFO path to use `$AUDIOBOOKS_RUN_DIR` variable
- **Scripts**: Fixed `set -e` failure in log function (changed `$VERBOSE && echo` to `if $VERBOSE; then echo`)

## [3.9.3] - 2026-01-08

### Changed

- **Periodicals (Reading Room)**: Simplified to flat data schema with skip list support
  - Each periodical is now a standalone item (matching Audible's content_type classification)
  - API endpoints use single `asin` instead of parent/child model
  - UI rewritten with details card view for better browsing
  - Added skip list support via `/etc/audiobooks/periodicals-skip.txt`
  - Content types: Podcast, Newspaper/Magazine, Show, Radio/TV Program

### Fixed

- **Mover Service**: Prevented `build-conversion-queue` process stampede
  - Added `flock -n` wrapper to prevent multiple concurrent rebuilds
  - Previously, 167+ zombie processes could accumulate consuming 200% CPU

## [3.9.2] - 2026-01-08

### Fixed

- **Reading Room API**: Fixed 500 Internal Server Error - all `get_db()` calls were missing required `db_path` argument
- **Periodicals Sync Service**: Fixed startup failure - removed non-existent `/var/log/audiobooks` from ReadWritePaths (service logs to systemd journal)

## [3.9.1] - 2026-01-08

### Fixed

- **Systemd Target**: All services now properly bind to `audiobook.target` for correct stop/start behavior during upgrades
  - Added `audiobook.target` to WantedBy for: api, proxy, redirect, periodicals-sync services and timer
  - Added explicit `Wants=` in audiobook.target for all core services and timers
  - Previously only converter/mover responded to `systemctl stop/start audiobook.target`

## [3.9.0] - 2026-01-08

### Added

- **Periodicals "Reading Room"**: New subsystem for episodic Audible content
  - Dedicated page for browsing podcasts, newspapers, meditation series
  - Category filtering (All, Podcasts, News, Meditation, Other)
  - Episode selection with bulk download capability
  - Real-time sync status via Server-Sent Events (SSE)
  - **On-demand refresh button** to sync periodicals index from Audible
  - Twice-daily automatic sync via systemd timer (06:00, 18:00)
  - Skip list integration - periodicals excluded from main library by default
- **Periodicals API Endpoints**:
  - `GET /api/v1/periodicals` - List all periodical parents with counts
  - `GET /api/v1/periodicals/<asin>` - List episodes for a parent
  - `GET /api/v1/periodicals/<asin>/<ep>` - Episode details
  - `POST /api/v1/periodicals/download` - Queue episodes for download
  - `DELETE /api/v1/periodicals/download/<asin>` - Cancel queued download
  - `GET /api/v1/periodicals/sync/status` - SSE stream for sync status
  - `POST /api/v1/periodicals/sync/trigger` - Manually trigger sync
  - `GET /api/v1/periodicals/categories` - List categories with counts
- **New Database Tables**: `periodicals` (content index), `periodicals_sync_status` (sync tracking)
- **New Systemd Units**: `audiobook-periodicals-sync.service`, `audiobook-periodicals-sync.timer`
- **Security**: XSS-safe DOM rendering using textContent and createElement (no innerHTML)
- **Technology**: HTMX for declarative interactions, SSE for real-time updates

### Changed

- **Library Header**: Added "Reading Room" navigation link next to "Back Office"
- **CSS Layout**: Header navigation now uses flex container for multiple links

### Fixed

- **Security**: Pinned minimum versions for transitive dependencies with CVEs
  - urllib3>=2.6.3 (CVE-2026-21441)
  - h11>=0.16.0 (CVE-2025-43859)
- **Security**: Fixed exception info exposure in position_sync.py (now returns generic error messages)
- **Code Cleanup**: Removed dead CSS code (banker-lamp classes) from utilities.css

## [3.8.0] - 2026-01-07

### Added

- **Position Sync with Audible**: Bidirectional playback position synchronization with Audible cloud
  - "Furthest ahead wins" conflict resolution - you never lose progress
  - Seamlessly switch between Audible apps and self-hosted library
  - Sync single books or batch sync all audiobooks with ASINs
  - Position history tracking for debugging and progress review
- **Position Sync API Endpoints**:
  - `GET /api/position/<id>` - Get position for a single audiobook
  - `PUT /api/position/<id>` - Update local playback position (from web player)
  - `POST /api/position/sync/<id>` - Sync single book with Audible
  - `POST /api/position/sync-all` - Batch sync all books with ASINs
  - `GET /api/position/syncable` - List all syncable audiobooks
  - `GET /api/position/history/<id>` - Get position history for a book
  - `GET /api/position/status` - Check if position sync is available
- **Web Player Integration**: Dual-layer position storage (localStorage + API)
  - Automatic position save every 15 seconds during playback
  - Resume from best position (furthest ahead from cache or API)
  - Immediate flush on player close
- **Credential Management**: Encrypted Audible auth password storage using Fernet (PBKDF2)
- **ASIN Population Tool**: `rnd/populate_asins.py` matches local books to Audible library
- **Documentation**: New comprehensive `docs/POSITION_SYNC.md` guide with:
  - Setup prerequisites and configuration steps
  - First sync instructions with batch-sync command
  - Ongoing sync maintenance patterns
  - API reference with examples
  - Troubleshooting guide

### Changed

- **Architecture Docs**: Added Position Sync Architecture section with data flow diagrams
- **README**: Added Position Sync section with quick setup guide

## [3.7.2] - 2026-01-07

### Added

- **Position Sync with Audible**: Bidirectional playback position synchronization with Audible cloud
  - "Furthest ahead wins" conflict resolution - you never lose progress
  - Seamlessly switch between Audible apps and self-hosted library
  - Sync single books or batch sync all audiobooks with ASINs
  - Position history tracking for debugging and progress review
- **Position Sync API Endpoints**:
  - `GET /api/position/<id>` - Get position for a single audiobook
  - `PUT /api/position/<id>` - Update local playback position (from web player)
  - `POST /api/position/sync/<id>` - Sync single book with Audible
  - `POST /api/position/sync-all` - Batch sync all books with ASINs
  - `GET /api/position/syncable` - List all syncable audiobooks
  - `GET /api/position/history/<id>` - Get position history for a book
  - `GET /api/position/status` - Check if position sync is available
- **Web Player Integration**: Dual-layer position storage (localStorage + API)
  - Automatic position save every 15 seconds during playback
  - Resume from best position (furthest ahead from cache or API)
  - Immediate flush on player close
- **Credential Management**: Encrypted Audible auth password storage using Fernet (PBKDF2)
- **ASIN Population Tool**: `rnd/populate_asins.py` matches local books to Audible library
- **Documentation**: New comprehensive `docs/POSITION_SYNC.md` guide with:
  - Setup prerequisites and configuration steps
  - First sync instructions with batch-sync command
  - Ongoing sync maintenance patterns
  - API reference with examples
  - Troubleshooting guide

### Changed

- **Architecture Docs**: Added Position Sync Architecture section with data flow diagrams
- **README**: Added Position Sync section with quick setup guide
- **Service Management**: Renamed `audiobooks-scanner.timer` to `audiobook-downloader.timer` in API
  and helper script to match actual systemd unit name

### Fixed

- **Download Feature**: Fixed "Read-only file system" error when downloading audiobooks
  - Added `/run/audiobooks` to `ReadWritePaths` in API service for lock files and temp storage
- **Vacuum Database**: Fixed "disk I/O error" when vacuuming database
  - Added `PRAGMA temp_store = MEMORY` to avoid temp file creation in sandboxed environment
- **Service Timer Control**: Fixed "Unit not found" error when starting/stopping timer
  - Updated service name from `audiobooks-scanner.timer` to `audiobook-downloader.timer`

## [3.7.1] - 2026-01-05

### Added

- **Duplicate Deletion**: Added delete capability for checksum-based duplicates in Back Office
  - New API endpoint `POST /api/duplicates/delete-by-path` for path-based deletion
  - Library checksum duplicates now show checkboxes for selection
  - Source checksum duplicates also support deletion (file-only, not in database)
  - Removed "manual deletion required" notice - duplicates can now be deleted from the UI

### Changed

- **Service Management**: Renamed `audiobooks-scanner.timer` to `audiobook-downloader.timer` in API
  and helper script to match actual systemd unit name
- **API Service**: Updated systemd service `ReadWritePaths` to include Library and Sources directories
  - Required for API to delete duplicate files (previously had read-only access)

### Fixed

- **Download Feature**: Fixed "Read-only file system" error when downloading audiobooks
  - Added runtime directory to `ReadWritePaths` in API service for lock files and temp storage
- **Vacuum Database**: Fixed "disk I/O error" when vacuuming database
  - Added `PRAGMA temp_store = MEMORY` to avoid temp file creation in sandboxed environment
- **Service Timer Control**: Fixed "Unit not found" error when starting/stopping timer
  - Updated service name from `audiobooks-scanner.timer` to `audiobook-downloader.timer`

## [3.7.0.1] - 2026-01-04

### Changed

- **Documentation**: Mark v3.5.x as end-of-life (no security patches or updates)

## [3.7.0] - 2026-01-04

### Changed

- **UI Styling**: Changed dark green text on dark backgrounds to cream-light for better contrast
  - Progress output text, success stats, active file indicators now use `--cream-light`

### Fixed

- **upgrade.sh**: Fixed non-interactive upgrade failures in systemd service
  - Fixed arithmetic increment `((issues_found++))` causing exit code 1 with `set -e`
  - Changed to `issues_found=$((issues_found + 1))` which always succeeds
- **upgrade-helper-process**: Auto-confirm upgrade prompts
  - Pipe "y" to upgrade script since user already confirmed via web UI
  - Fixes `read` command failing with no TTY in systemd context

## [3.6.4.1] - 2026-01-04

### Added

- **CSS Customization Guide**: New `docs/CSS-CUSTOMIZATION.md` documenting how to customize
  colors, fonts, shadows, and create custom themes for the web UI

### Changed

- **UI Styling**: Enhanced visual depth and contrast across web interface
  - Darkened header sunburst background for better separation from content
  - Brightened all cream-colored text (85% opacity → 100% with cream-light color)
  - Added shadow elevation system to theme for consistent depth cues
  - Matched Back Office header/background styling to main Library page
- **Back Office**: Removed hardcoded version from header (available in System tab)

### Fixed

- **Upgrade Button**: Fixed confirm dialog always resolving as "Cancel"
  - `confirmAction()` was resolving with `false` before `resolve(true)` could run
  - Clicking "Confirm" on upgrade dialog now properly triggers the upgrade
- **Duplicate Detection**: Improved detection of already-converted audiobooks
  - Added word-set matching for titles with same words in different order
    (e.g., "Bill Bryson's... Ep. 1: Title" vs "Ep. 1: Title (Bill Bryson's...)")
  - Added title fallback matching for ASIN files (catches same-book-different-ASIN scenarios)
  - Added 2-word prefix matching for title variations
    (e.g., "Blue Belle Burke Book 3" matches "Blue Belle: A Burke Novel 3")

## [3.6.4] - 2026-01-04

### Fixed

- **upgrade.sh**: Self-healing tarball extraction with flexible pattern matching
  - Now tries multiple directory patterns (`audiobook-manager-*`, `audiobooks-*`, `Audiobook-Manager-*`)
  - Fallback pattern for any versioned directory (`*-[0-9]*`)
  - Added debug output showing temp dir contents on extraction failure
  - Prevents bootstrap problems where old upgrade scripts can't upgrade themselves

## [3.6.3] - 2026-01-03

### Fixed

- **upgrade.sh**: Fixed GitHub release extraction failing with "Could not find extracted directory"
  - Changed glob pattern from `audiobooks-*` to `audiobook-manager-*` to match actual tarball structure
- **upgrade.sh**: Fixed project upgrade (`--from-project`) failing with exit code 1 when no upgrade needed
  - Now exits cleanly with code 0 when versions are identical (matches GitHub mode behavior)
  - Fixes web UI upgrade from project showing "Upgrade failed" when already up to date

## [3.6.2] - 2026-01-03

### Changed

- **utilities_system.py**: Project discovery now searches multiple paths instead of hardcoded
  `/hddRaid1/ClaudeCodeProjects` - checks `AUDIOBOOKS_PROJECT_DIR` env, `~/ClaudeCodeProjects`,
  `~/projects`, and `/opt/projects`

### Fixed

- Version sync: Updated `install-manifest.json`, `Dockerfile`, `CLAUDE.md`, and
  `docs/ARCHITECTURE.md` to match VERSION file (3.6.1 → now 3.6.2)
- Removed unused imports in `scan_audiobooks.py` (re-exported from `metadata_utils` for
  backwards compatibility with tests)
- Added `.claudeignore` to exclude `.snapshots/` from Claude Code settings scanning

## [3.6.1] - 2026-01-03

### Added

- **Privilege-separated helper service**: System operations (service control, upgrades) now work
  with the API's `NoNewPrivileges=yes` security hardening via a helper service pattern
  - `audiobook-upgrade-helper.service`: Runs privileged operations as root
  - `audiobook-upgrade-helper.path`: Watches for request files to trigger helper
  - Control files stored in `/var/lib/audiobooks/.control/` (avoids systemd namespace issues)

### Changed

- **API utilities_system.py**: Refactored from direct sudo calls to file-based IPC with helper
- **install.sh/upgrade.sh**: Now deploy the helper service units

### Fixed

- Service control (start/stop/restart) from web UI now works with sandboxed API
- Upgrade from web UI now works with `NoNewPrivileges=yes` security hardening
- Race condition in status polling that caused false failure responses

## [3.6.0] - 2026-01-03

### Added

- **Audible Sync tab**: New Back Office section for syncing metadata from Audible library exports
  - Sync Genres: Match audiobooks to Audible entries and populate genre fields
  - Update Narrators: Fill in missing narrator information from Audible data
  - Populate Sort Fields: Generate author_sort and title_sort for proper alphabetization
  - Prerequisites check: Verifies library_metadata.json exists before operations
- **Pipeline Operations**: Download Audiobooks, Rebuild Queue, Cleanup Indexes accessible from UI
- **Tooltips**: Comprehensive tooltips on all buttons and action items for discoverability
- **CSS modular architecture**: Separated styles into focused modules:
  - `theme-art-deco.css`: Art Deco color palette, typography, decorative elements
  - `layout.css`: Grid systems, card layouts, responsive breakpoints
  - `components.css`: Buttons, badges, status indicators, forms
  - `sidebar.css`: Collections panel with pigeon-hole design
  - `player.css`: Audio player styling
  - `modals.css`: Dialog and modal styling
- **Check Audible Prerequisites endpoint**: `/api/utilities/check-audible-prereqs`

### Changed

- **Art Deco theme applied globally**: Complete visual redesign across entire application:
  - Dark geometric diamond background pattern
  - Gold, cream, and charcoal color palette
  - Sunburst headers with chevron borders
  - Stepped corners on book cards
  - High-contrast dark inputs and dropdowns
  - Enhanced banker's lamp SVG with glow effect
  - Filing cabinet tab navigation with pigeon-hole metaphor
- Updated Python script API endpoints to use `--execute` flag (dry-run is default)
- Improved column balance with `align-items: stretch` for equal card heights
- Database tab reorganized into balanced 2x2 card layout

### Fixed

- Removed duplicate API endpoint definitions causing Flask startup failures
- Fixed bash `log()` functions to work with `set -e` (use if/then instead of &&)
- Fixed genre sync, narrator sync, and sort field population API argument handling
- Fixed cream-on-cream contrast issues in Back Office intro cards
- Fixed light background on form inputs and dropdowns throughout application

## [3.5.0] - 2026-01-03

> ⚠️ **END OF LIFE - NO LONGER SUPPORTED**
>
> The 3.5.x branch reached end-of-life with the release of v3.7.0.
>
> - **No further updates** will be released for 3.5.x
> - **No security patches** - upgrade to 3.7.0+ immediately
> - **Migration required**: v3.5.0 was the last version supporting the legacy monolithic API (`api.py`)
>
> Users still on 3.5.x must upgrade to v3.7.0 or later. See [upgrade documentation](docs/ARCHITECTURE.md).

### Added

- **Checksum tracking**: MD5 checksums (first 1MB) generated automatically during download and move operations
- **Generate Checksums button**: New Utilities maintenance feature for Sources AND Library with hover tooltips
- **Index cleanup script**: `cleanup-stale-indexes` removes entries for deleted files from all indexes
- Automatic index cleanup: Deleted files are removed from checksum indexes via delete operations
- Real-time index updates after each conversion completes
- Prominent remaining summary box in Conversion Monitor
- Inline database import in Back Office UI

### Changed

- **Bulk Operations redesign**: Clear step-by-step workflow with explanatory intro, descriptive filter options, and use-case examples
- **Conversion queue**: Hybrid ASIN + title matching for accurate queue building
- Removed redundant "Audiobooks" tab from Back Office (audiobook search available on main library page)
- Updated "Generate Hashes" button tooltip to clarify it regenerates ALL hashes
- Download and mover services now append checksums to index files in real-time
- Mover timing optimization: reduced file age check from 5min to 1min, polling from 5min to 30sec

### Fixed

- Fixed chapters.json ASIN extraction in cleanup script (ASINs are in JSON content, not filename)
- Queue builder robustness: title normalization, subshell issues, edition handling
- Version display fixes in Back Office header

## [3.4.2] - 2026-01-02

### Changed

- Refactored utilities.py (1067 lines) into 4 focused sub-modules:
  - `utilities_crud.py`: CRUD operations (259 lines)
  - `utilities_db.py`: Database maintenance (291 lines)
  - `utilities_ops.py`: Async operations with progress tracking (322 lines)
  - `utilities_conversion.py`: Conversion monitoring with extracted helpers (294 lines)
- Refactored scanner modules with new shared `metadata_utils.py`:
  - Extracted genre taxonomy, topic keywords, and metadata extraction helpers
  - `scan_audiobooks.py`: D(24) → A(3) complexity on main function
  - `add_new_audiobooks.py`: D(21) → C(13) max complexity
  - Average scanner complexity now B(5.2)
- Reduced average cyclomatic complexity from D (high) to A (3.7)
- Extracted helper functions (`get_ffmpeg_processes`, `parse_job_io`, `get_system_stats`) for testability

### Fixed

- Fixed conversion progress showing "100% Complete" while active FFmpeg processes still running
- Fixed REMAINING and QUEUE SIZE showing 0 when conversions are in-progress (now shows active count)
- Removed unused imports and variables (code cleanup)
- Removed orphaned test fixtures from conftest.py
- Updated Dockerfile version default to match current VERSION

## [3.4.1] - 2026-01-02

### Added

- Comprehensive ARCHITECTURE.md guide with:
  - System component diagrams and symlink architecture
  - Install, upgrade, and migrate workflow diagrams
  - Storage tier recommendations by component type
  - Filesystem recommendations (ext4, XFS, Btrfs, ZFS, F2FS)
  - Kernel compatibility matrix (LTS through rolling release)
  - I/O scheduler recommendations
- Installed directory structure documentation in README.md

### Changed

- `install.sh` now uses `/opt/audiobooks` as canonical install location instead of `/usr/local/lib/audiobooks`
- Wrapper scripts now source from `/opt/audiobooks/lib/audiobook-config.sh` (canonical path)
- Added backward-compatibility symlink `/usr/local/lib/audiobooks` → `/opt/audiobooks/lib/`
- `install.sh` now automatically enables and starts services after installation (no manual step needed)
- `migrate-api.sh` now stops services before migration and starts them after (proper lifecycle management)
- `/etc/profile.d/audiobooks.sh` now sources from canonical `/opt/audiobooks/lib/` path

### Fixed

- Fixed `install.sh` to create symlinks in `/usr/local/bin/` instead of copying scripts
- Fixed proxy server to forward `/covers/` requests to API backend

## [3.4.0] - 2026-01-02

### Added

- Per-job conversion stats with progress percentage and throughput (MiB/s)
- Sortable Active Conversions list (by percent, throughput, or name)
- Expandable conversion details panel in Back Office UI
- Text-search based collection subgenres: Short Stories & Anthologies, Action & Adventure, Historical Fiction
- Short Stories collection detects: editor in author field, ": Stories" suffix, "Complete/Collected" patterns

### Changed

- Active conversions now use light background with dark text for better readability
- Cover art now stored in data directory (`${AUDIOBOOKS_DATA}/.covers`) instead of application directory
- Config template uses `${AUDIOBOOKS_DATA}` references for portability across installations
- Scripts now installed to `/opt/audiobooks/scripts/` (canonical) with symlinks in `/usr/local/bin/`
- Clear separation: `/opt/audiobooks/` (application), `${AUDIOBOOKS_DATA}/` (user data), `/var/lib/` (database)

### Fixed

- **CRITICAL**: Fixed `DATA_DIR` config not reading from `/etc/audiobooks/audiobooks.conf`, which caused "Reimport Database" to read from test fixtures instead of production data
- Fixed collection genre queries to match actual database genre names (Fiction, Sci-Fi & Fantasy, etc.)
- Fixed queue count sync - now shows actual remaining files instead of stale queue.txt count
- Fixed cover serving to use `COVER_DIR` from config instead of hardcoded path
- Fixed proxy server to forward `/covers/` requests to API backend (was returning 404)
- Fixed `install.sh` to create symlinks in `/usr/local/bin/` instead of copying scripts (upgrades now automatically update commands)
- Removed false-positive Romance collection (was matching "Romantics" literary movement and "Neuromancer")
- Added test data validation in `import_to_db.py` to prevent importing test fixtures
- Fixed Docker entrypoint paths: `api.py` → `api_server.py`, `web-v2` → `web`
- Fixed UI contrast and added ionice for faster conversions
- Improved conversion details panel legibility and data display
- Cleaned up obsolete scripts and symlinks from user data directory

## [3.3.1] - 2026-01-01

### Changed

- Upgrade script now automatically stops services before upgrade and restarts them after
- Removed manual "Remember to restart services" reminder (now handled automatically)
- Service status summary displayed after upgrade completes

## [3.3.0] - 2026-01-01

### Added

- Conversion Monitor in Back Office web UI with real-time progress bar, rate calculation, and ETA
- `/api/conversion/status` endpoint returning file counts, active ffmpeg processes, and system stats
- ProgressTracker class in scanner with visual progress bar (█░), rate, and ETA display
- `build-conversion-queue` script for index-based queue building with ASIN + unique non-ASIN support
- `find-duplicate-sources` script for identifying duplicate .aaxc files
- Incremental audiobook scanner with progress tracking UI
- Ananicy rules for ffmpeg priority tuning during conversions

### Changed

- Scanner now shows visual progress bar instead of simple percentage output
- Conversion queue includes unique non-ASIN files that have no ASIN equivalent

### Fixed

- Type safety improvements across codebase
- Version sync between project files
- Duplicate file handling in source directory

## [3.2.1] - 2025-12-30

### Added

- Docker build job to release workflow for automated container builds

### Changed

- Increased default parallel conversion jobs from 8 to 12
- Removed redundant config fallbacks from scripts (single source of truth in audiobook-config.sh)

### Fixed

- Updated documentation to v3.2.0 and fixed obsolete paths

## [3.2.0] - 2025-12-29

### Added

- Standalone installation via GitHub releases (`bootstrap-install.sh`)
- GitHub-based upgrade system (`audiobook-upgrade --from-github`)
- Release automation workflow (`.github/workflows/release.yml`)
- Release tarball builder (`create-release.sh`)

### Changed

- Renamed repository from `audiobook-toolkit` to `Audiobook-Manager`
- Removed Flask-CORS dependency (CORS now handled natively)
- Updated all documentation to reflect new repository name

### Removed

- Deleted monolithic `api.py` (2,244 lines) - superseded by `api_modular/`
- Deleted legacy `web.legacy/` directory - superseded by `web-v2/`

### Fixed

- Flask blueprint double-registration error in `api_modular`
- SQL injection vulnerability in `generate_hashes.py`
- Configuration path mismatch after repository rename

## [3.1.1] - 2025-12-29

### Fixed

- RuntimeDirectoryMode changed from 0755 to 0775 to allow group write access, fixing permission errors when running downloader from desktop shortcuts

## [3.1.0] - 2025-12-29

### Added

- Install manifest (`install-manifest.json`) for production validation
- API architecture selection and migration tools (`migrate-api.sh`)
- Modular Flask Blueprint architecture (`api_modular/`)
- Deployment infrastructure with dev configuration
- Post-install permission verification with umask 022

### Changed

- Refactored codebase with linting fixes and test migration to api_modular

### Fixed

- Resolved 7 hanging tests by correcting mock paths in test suite
- Fixed 13 shellcheck warnings across shell scripts
- Resolved 18 mypy type errors across Python modules
- Addressed security vulnerabilities and code quality issues

## [3.0.5] - 2025-12-27

### Security

- Fixed SQL injection vulnerability in genre query functions
- Docker container now runs as non-root user
- Added input escaping for LIKE patterns

### Changed

- Pinned Docker base image to python:3.11.11-slim
- Standardized port configuration (8443 for HTTPS, 8080 for HTTP redirect)
- Updated Flask version constraint to >=3.0.0

### Added

- LICENSE file (MIT)
- CONTRIBUTING.md with contribution guidelines
- .env.example template for easier setup
- This CHANGELOG.md

## [3.0.0] - 2025-12-25

### Added

- Modular API architecture (api_modular/ blueprints)
- PDF supplements support with viewer
- Multi-source audiobook support (experimental)
- HTTPS support with self-signed certificates
- Docker multi-platform builds (amd64, arm64)

### Changed

- Migrated from monolithic api.py to Flask Blueprints
- Improved test coverage (234 tests)
- Enhanced deployment scripts with dry-run support

### Fixed

- Cover art extraction for various formats
- Database import performance improvements
- CORS configuration for cross-origin requests

## [2.0.0] - 2024-11-28

### Added

- Web-based audiobook browser
- Search and filtering capabilities
- Cover art display and caching
- Audiobook streaming support
- SQLite database backend
- Docker containerization
- Systemd service integration

### Changed

- Complete rewrite from shell scripts to Python/Flask

## [1.0.0] - 2024-09-15

### Added

- Initial release
- AAXtoMP3 converter integration
- Basic audiobook scanning
- JSON metadata export

[Unreleased]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.4.1...HEAD
[7.4.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.3.0.1...v7.4.1
[7.3.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.3.0...v7.3.0.1
[7.3.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.2.1.1...v7.3.0
[7.2.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.2.1...v7.2.1.1
[7.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.2.0...v7.2.1
[7.2.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3.4...v7.2.0
[7.1.3.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3.3...v7.1.3.4
[7.1.3.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3.2...v7.1.3.3
[7.1.3.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3.1...v7.1.3.2
[7.1.3.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.3...v7.1.3.1
[7.1.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.2.1...v7.1.3
[7.1.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.2...v7.1.2.1
[7.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.1.1...v7.1.2
[7.1.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.1...v7.1.1.1
[7.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.1.0...v7.1.1
[7.1.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.0.2...v7.1.0
[7.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.0.1...v7.0.2
[7.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v7.0.0...v7.0.1
[7.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2.4...v7.0.0
[6.7.2.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2.3...v6.7.2.4
[6.7.2.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2.2...v6.7.2.3
[6.7.2.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2.1...v6.7.2.2
[6.7.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.2...v6.7.2.1
[6.7.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.5...v6.7.2
[6.7.1.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.4...v6.7.1.5
[6.7.1.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.3...v6.7.1.4
[6.7.1.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.2...v6.7.1.3
[6.7.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1.1...v6.7.1.2
[6.7.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.1...v6.7.1.1
[6.7.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.0.3...v6.7.1
[6.7.0.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.0.2...v6.7.0.3
[6.7.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.0.1...v6.7.0.2
[6.7.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.7.0...v6.7.0.1
[6.7.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.7...v6.7.0
[6.6.7]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.6.1...v6.6.7
[6.6.6.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.6...v6.6.6.1
[6.6.6]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.5.1...v6.6.6
[6.6.5.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.5...v6.6.5.1
[6.6.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.4...v6.6.5
[6.6.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.3...v6.6.4
[6.6.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.6...v6.6.3
[6.6.2.6]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.5...v6.6.2.6
[6.6.2.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.4...v6.6.2.5
[6.6.2.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.3...v6.6.2.4
[6.6.2.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.2...v6.6.2.3
[6.6.2.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2.1...v6.6.2.2
[6.6.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.2...v6.6.2.1
[6.6.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.1.1...v6.6.2
[6.6.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.1...v6.6.1.1
[6.6.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.6.0...v6.6.1
[6.6.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.5.0.1...v6.6.0
[6.5.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.5.0...v6.5.0.1
[6.5.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.4.0.1...v6.5.0
[6.4.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.4.0...v6.4.0.1
[6.4.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.3.0...v6.4.0
[6.3.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.2.0.1...v6.3.0
[6.2.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.2.0...v6.2.0.1
[6.2.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.3...v6.2.0
[6.1.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.2.1...v6.1.3
[6.1.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.2...v6.1.2.1
[6.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.1...v6.1.2
[6.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.1.0...v6.1.1
[6.1.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v6.0.0...v6.1.0
[6.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v5.0.2...v6.0.0
[5.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v5.0.1.1...v5.0.2
[5.0.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v5.0.1...v5.0.1.1
[5.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v5.0.0...v5.0.1
[5.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.1.2...v5.0.0
[4.1.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.1.1...v4.1.2
[4.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.1.0...v4.1.1
[4.1.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.5...v4.1.0
[4.0.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.4...v4.0.5
[4.0.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.3...v4.0.4
[4.0.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.2...v4.0.3
[4.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.1...v4.0.2
[4.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.0.2...v4.0.1
[4.0.0.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.0.1...v4.0.0.2
[4.0.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v4.0.0...v4.0.0.1
[4.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.11.2...v4.0.0
[3.11.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.11.1...v3.11.2
[3.11.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.11.0...v3.11.1
[3.11.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.10.1...v3.11.0
[3.10.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.8...v3.10.0
[3.9.8]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.7...v3.9.8
[3.9.7]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.6...v3.9.7
[3.9.6]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.5...v3.9.6
[3.9.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.4...v3.9.5
[3.9.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.3...v3.9.4
[3.9.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.2...v3.9.3
[3.9.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.1...v3.9.2
[3.9.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.9.0...v3.9.1
[3.9.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.7.2...v3.8.0
[3.7.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.7.1...v3.7.2
[3.7.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.7.0.1...v3.7.1
[3.7.0.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.7.0...v3.7.0.1
[3.7.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.4.1...v3.7.0
[3.6.4.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.4...v3.6.4.1
[3.6.4]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.3...v3.6.4
[3.6.3]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.2...v3.6.3
[3.6.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.1...v3.6.2
[3.6.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.6.0...v3.6.1
[3.6.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.4.2...v3.5.0
[3.4.2]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.4.1...v3.4.2
[3.4.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.4.0...v3.4.1
[3.4.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.3.1...v3.4.0
[3.3.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.3.0...v3.3.1
[3.3.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.2.1...v3.3.0
[3.2.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.2.0...v3.2.1
[3.2.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.1.1...v3.2.0
[3.1.1]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.0.5...v3.1.0
[3.0.5]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v3.0.0...v3.0.5
[3.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v2.0.0...v3.0.0
[2.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/TheBoscoClub/Audiobook-Manager/releases/tag/v1.0.0
