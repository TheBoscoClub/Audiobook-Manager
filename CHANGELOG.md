# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed
- Fixed conversion progress showing "100% Complete" while active FFmpeg processes still running
- Fixed REMAINING and QUEUE SIZE showing 0 when conversions are in-progress (now shows active count)

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
- Wrapper scripts now source from `/opt/audiobooks/lib/audiobooks-config.sh` (canonical path)
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
- Removed redundant config fallbacks from scripts (single source of truth in audiobooks-config.sh)

### Fixed
- Updated documentation to v3.2.0 and fixed obsolete paths

## [3.2.0] - 2025-12-29

### Added
- Standalone installation via GitHub releases (`bootstrap-install.sh`)
- GitHub-based upgrade system (`audiobooks-upgrade --from-github`)
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
