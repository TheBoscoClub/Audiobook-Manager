# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

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
