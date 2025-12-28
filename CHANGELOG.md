# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
