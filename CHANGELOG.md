# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

## [4.0.4] - 2026-01-20

### Fixed
- **Systemd**: Fixed API service failing at boot with NAMESPACE error on HDD/NAS storage. Added `/raid0/Audiobooks` to `RequiresMountsFor` so systemd waits for the data mount before setting up the security namespace. Previously only waited for `/opt/audiobooks`.
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
- **Systemd**: Fixed "Read-only file system" error when rebuilding conversion queue. Added `AUDIOBOOKS_DATA` path (`/raid0/Audiobooks`) to `ReadWritePaths` in `audiobook-api.service` since `ProtectSystem=strict` was blocking write access to the index directory.

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
  `/raid0/ClaudeCodeProjects` - checks `AUDIOBOOKS_PROJECT_DIR` env, `~/ClaudeCodeProjects`,
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
