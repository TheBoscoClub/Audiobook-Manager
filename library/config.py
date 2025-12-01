#!/usr/bin/env python3
"""
Audiobook Library Configuration Module
======================================
Provides centralized configuration for all Python scripts.

Configuration priority (later overrides earlier):
    1. Built-in defaults
    2. /etc/audiobooks/audiobooks.conf (system config)
    3. ~/.config/audiobooks/audiobooks.conf (user config)
    4. Legacy config.env in project root (backwards compatibility)
    5. Environment variables (highest priority)
"""

import os
from pathlib import Path
from typing import Optional

# =============================================================================
# Configuration Loading
# =============================================================================

def _load_config_file(filepath: Path) -> dict:
    """Load configuration from a shell-style config file."""
    config = {}
    if not filepath.exists():
        return config

    with open(filepath) as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue

            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            # Handle variable substitution (simple form)
            if '${' in value:
                for var, val in config.items():
                    value = value.replace('${' + var + '}', val)
                # Also check environment
                import re
                for match in re.findall(r'\$\{(\w+)\}', value):
                    env_val = os.environ.get(match, config.get(match, ''))
                    value = value.replace('${' + match + '}', env_val)

            config[key] = value

    return config


def _find_project_root() -> Optional[Path]:
    """Find project root by looking for marker files."""
    current = Path(__file__).parent
    markers = ['config.env', 'lib/audiobooks-config.sh', 'library/config.py']

    while current != current.parent:
        for marker in markers:
            if (current / marker).exists():
                return current
        # Also check if we're in library/ subdirectory
        if current.name == 'library' and (current.parent / 'lib').exists():
            return current.parent
        current = current.parent

    return None


# Load configuration from all sources
_config = {}

# 1. System config
_config.update(_load_config_file(Path('/etc/audiobooks/audiobooks.conf')))

# 2. User config
_user_config = Path.home() / '.config' / 'audiobooks' / 'audiobooks.conf'
_config.update(_load_config_file(_user_config))

# 3. Legacy config.env (backwards compatibility)
_project_root = _find_project_root()
if _project_root:
    _config.update(_load_config_file(_project_root / 'config.env'))


def get_config(key: str, default: str = None) -> str:
    """Get configuration value with environment override."""
    return os.environ.get(key, _config.get(key, default))


# =============================================================================
# Core Paths
# =============================================================================

# Auto-detect AUDIOBOOKS_HOME if not set
_detected_home = str(_project_root) if _project_root else None
AUDIOBOOKS_HOME = Path(get_config('AUDIOBOOKS_HOME', _detected_home or '/opt/audiobooks'))

# Core data directory
AUDIOBOOKS_DATA = Path(get_config('AUDIOBOOKS_DATA', '/srv/audiobooks'))

# Data subdirectories
AUDIOBOOKS_LIBRARY = Path(get_config('AUDIOBOOKS_LIBRARY', str(AUDIOBOOKS_DATA / 'Library')))
AUDIOBOOKS_SOURCES = Path(get_config('AUDIOBOOKS_SOURCES', str(AUDIOBOOKS_DATA / 'Sources')))
AUDIOBOOKS_SUPPLEMENTS = Path(get_config('AUDIOBOOKS_SUPPLEMENTS', str(AUDIOBOOKS_DATA / 'Supplements')))

# Application paths
AUDIOBOOKS_DATABASE = Path(get_config('AUDIOBOOKS_DATABASE',
    str(AUDIOBOOKS_HOME / 'library' / 'backend' / 'audiobooks.db')))
AUDIOBOOKS_COVERS = Path(get_config('AUDIOBOOKS_COVERS',
    str(AUDIOBOOKS_HOME / 'library' / 'web-v2' / 'covers')))
AUDIOBOOKS_CERTS = Path(get_config('AUDIOBOOKS_CERTS',
    str(AUDIOBOOKS_HOME / 'library' / 'certs')))
AUDIOBOOKS_LOGS = Path(get_config('AUDIOBOOKS_LOGS',
    str(AUDIOBOOKS_DATA / 'logs')))
AUDIOBOOKS_VENV = Path(get_config('AUDIOBOOKS_VENV',
    str(AUDIOBOOKS_HOME / 'library' / 'venv')))
AUDIOBOOKS_CONVERTER = Path(get_config('AUDIOBOOKS_CONVERTER',
    str(AUDIOBOOKS_HOME / 'converter' / 'AAXtoMP3')))

# Server settings
AUDIOBOOKS_API_PORT = int(get_config('AUDIOBOOKS_API_PORT', '5001'))
AUDIOBOOKS_WEB_PORT = int(get_config('AUDIOBOOKS_WEB_PORT', '8090'))
AUDIOBOOKS_HTTP_REDIRECT_PORT = int(get_config('AUDIOBOOKS_HTTP_REDIRECT_PORT', '8081'))
AUDIOBOOKS_BIND_ADDRESS = get_config('AUDIOBOOKS_BIND_ADDRESS', '0.0.0.0')
AUDIOBOOKS_HTTPS_ENABLED = get_config('AUDIOBOOKS_HTTPS_ENABLED', 'true').lower() in ('true', '1', 'yes')
AUDIOBOOKS_HTTP_REDIRECT_ENABLED = get_config('AUDIOBOOKS_HTTP_REDIRECT_ENABLED', 'true').lower() in ('true', '1', 'yes')

# =============================================================================
# Legacy Aliases (backwards compatibility)
# =============================================================================

# Check for legacy environment variables first (Docker compatibility)
# Then fall back to new naming convention
PROJECT_DIR = Path(os.environ.get('PROJECT_DIR', str(AUDIOBOOKS_HOME)))
LIBRARY_DIR = PROJECT_DIR / 'library' if PROJECT_DIR else Path('.')
AUDIOBOOK_DIR = Path(os.environ.get('AUDIOBOOK_DIR', str(AUDIOBOOKS_LIBRARY)))
DATABASE_PATH = Path(os.environ.get('DATABASE_PATH', str(AUDIOBOOKS_DATABASE)))
COVER_DIR = Path(os.environ.get('COVER_DIR', str(AUDIOBOOKS_COVERS)))
DATA_DIR = Path(os.environ.get('DATA_DIR', str(PROJECT_DIR / 'library' / 'data' if PROJECT_DIR else Path('.'))))
SOURCES_DIR = AUDIOBOOKS_SOURCES
SUPPLEMENTS_DIR = Path(os.environ.get('SUPPLEMENTS_DIR', str(AUDIOBOOKS_SUPPLEMENTS)))
OPUS_DIR = AUDIOBOOK_DIR  # Points to same as AUDIOBOOK_DIR
CONVERTED_DIR = AUDIOBOOK_DIR  # Points to same as AUDIOBOOK_DIR
WEB_PORT = int(os.environ.get('WEB_PORT', str(AUDIOBOOKS_WEB_PORT)))
API_PORT = int(os.environ.get('API_PORT', str(AUDIOBOOKS_API_PORT)))

# =============================================================================
# Utility Functions
# =============================================================================

def print_config():
    """Print current configuration for debugging."""
    print("Audiobook Library Configuration")
    print("=" * 50)
    print(f"AUDIOBOOKS_HOME:        {AUDIOBOOKS_HOME}")
    print(f"AUDIOBOOKS_DATA:        {AUDIOBOOKS_DATA}")
    print(f"AUDIOBOOKS_LIBRARY:     {AUDIOBOOKS_LIBRARY}")
    print(f"AUDIOBOOKS_SOURCES:     {AUDIOBOOKS_SOURCES}")
    print(f"AUDIOBOOKS_SUPPLEMENTS: {AUDIOBOOKS_SUPPLEMENTS}")
    print(f"AUDIOBOOKS_DATABASE:    {AUDIOBOOKS_DATABASE}")
    print(f"AUDIOBOOKS_COVERS:      {AUDIOBOOKS_COVERS}")
    print(f"AUDIOBOOKS_CERTS:       {AUDIOBOOKS_CERTS}")
    print(f"AUDIOBOOKS_LOGS:        {AUDIOBOOKS_LOGS}")
    print(f"AUDIOBOOKS_VENV:        {AUDIOBOOKS_VENV}")
    print(f"AUDIOBOOKS_CONVERTER:   {AUDIOBOOKS_CONVERTER}")
    print(f"AUDIOBOOKS_API_PORT:    {AUDIOBOOKS_API_PORT}")
    print(f"AUDIOBOOKS_WEB_PORT:    {AUDIOBOOKS_WEB_PORT} (HTTPS)")
    print(f"AUDIOBOOKS_HTTP_REDIRECT_PORT: {AUDIOBOOKS_HTTP_REDIRECT_PORT}")
    print(f"AUDIOBOOKS_HTTP_REDIRECT_ENABLED: {AUDIOBOOKS_HTTP_REDIRECT_ENABLED}")
    print(f"AUDIOBOOKS_BIND_ADDRESS: {AUDIOBOOKS_BIND_ADDRESS}")
    print(f"AUDIOBOOKS_HTTPS_ENABLED: {AUDIOBOOKS_HTTPS_ENABLED}")
    print("=" * 50)


def check_dirs() -> bool:
    """Verify required directories exist. Returns True if all exist."""
    required = [
        AUDIOBOOKS_LIBRARY,
        AUDIOBOOKS_DATABASE.parent,
    ]
    missing = []
    for d in required:
        if not d.exists():
            missing.append(str(d))
    if missing:
        print("Warning: Missing directories:", file=__import__('sys').stderr)
        for m in missing:
            print(f"  - {m}", file=__import__('sys').stderr)
        return False
    return True


if __name__ == "__main__":
    print_config()
