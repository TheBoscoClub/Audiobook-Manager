# Changelog

All notable changes to this fork will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1] - 2025-11-22

### Changed
- Updated fork version information in FORK_README.md to reflect v2.1
- Updated "Last Updated" date to 2025-11-22
- Incremented commit count to 8

### Documentation
- Clarified fork status and version tracking

## [2.0] - 2025-11-21

### Added
- Comprehensive security documentation (SECURITY.md)
- Multi-distribution installation instructions (Arch/CachyOS/Manjaro)
- Repository tags and metadata improvements
- Comprehensive attribution and legal disclaimers
- Fork documentation (FORK_README.md)

### Fixed
- **Fixed unbound variable error**: Script would crash with `tmp_chapter_file: unbound variable` when chapter files were missing
- **Fixed cover extraction for AAXC files**: Used hardcoded `-activation_bytes` parameter which doesn't work for AAXC
- Made chapter/cover file validation non-fatal - script now warns but continues

### Changed
- Enhanced AAXC fallback handling and logging
- Made audible-cli chapter and cover files optional
- Improved user feedback with informative log messages

### Documentation
- Clarified testing environment (CachyOS only)
- Added platform support expectations
- Enhanced attribution to original authors
- Added legal compliance documentation

## [1.3] - Original Upstream

This fork is based on [KrumpetPirate/AAXtoMP3](https://github.com/KrumpetPirate/AAXtoMP3) v1.3 (now archived).

For changes prior to this fork, see the original repository's git history.
