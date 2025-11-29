#!/usr/bin/env python3
"""
Audiobook Library Configuration Module
Provides centralized configuration for all Python scripts.

Configuration priority:
1. Environment variables (highest priority)
2. config.env file in project root
3. Default values (lowest priority)
"""

import os
from pathlib import Path

def _load_config_env():
    """Load configuration from config.env file"""
    config = {}

    # Find project root (where config.env lives)
    current = Path(__file__).parent
    while current != current.parent:
        config_file = current / "config.env"
        if config_file.exists():
            with open(config_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        # Handle variable substitution
                        if '${' in value:
                            for var, val in config.items():
                                value = value.replace('${' + var + '}', val)
                        config[key] = value
            break
        current = current.parent

    return config

# Load config.env
_config = _load_config_env()

def get_config(key: str, default: str = None) -> str:
    """Get configuration value with environment override"""
    return os.environ.get(key, _config.get(key, default))

# =============================================================================
# Project Paths
# =============================================================================

# Auto-detect project directory
_default_project_dir = str(Path(__file__).parent.parent.absolute())

PROJECT_DIR = Path(get_config('PROJECT_DIR', _default_project_dir))
LIBRARY_DIR = PROJECT_DIR / "library"

# =============================================================================
# Audiobook Paths
# =============================================================================

AUDIOBOOK_DIR = Path(get_config('AUDIOBOOK_DIR', '/raid0/Audiobooks'))

# =============================================================================
# Database
# =============================================================================

DATABASE_PATH = Path(get_config('DATABASE_PATH', str(LIBRARY_DIR / "backend" / "audiobooks.db")))

# =============================================================================
# Data Directories
# =============================================================================

COVER_DIR = Path(get_config('COVER_DIR', str(LIBRARY_DIR / "web" / "covers")))
DATA_DIR = Path(get_config('DATA_DIR', str(LIBRARY_DIR / "data")))

# =============================================================================
# Server Ports
# =============================================================================

WEB_PORT = int(get_config('WEB_PORT', '8090'))
API_PORT = int(get_config('API_PORT', '5001'))

# =============================================================================
# Conversion Paths
# =============================================================================

OPUS_DIR = Path(get_config('OPUS_DIR', str(AUDIOBOOK_DIR / "Audiobooks-Converted-Opus-nocomp")))
CONVERTED_DIR = Path(get_config('CONVERTED_DIR', str(AUDIOBOOK_DIR / "converted")))

# =============================================================================
# Utility Functions
# =============================================================================

def print_config():
    """Print current configuration for debugging"""
    print("Audiobook Library Configuration")
    print("=" * 40)
    print(f"PROJECT_DIR:    {PROJECT_DIR}")
    print(f"AUDIOBOOK_DIR:  {AUDIOBOOK_DIR}")
    print(f"DATABASE_PATH:  {DATABASE_PATH}")
    print(f"COVER_DIR:      {COVER_DIR}")
    print(f"DATA_DIR:       {DATA_DIR}")
    print(f"WEB_PORT:       {WEB_PORT}")
    print(f"API_PORT:       {API_PORT}")
    print("=" * 40)

if __name__ == "__main__":
    print_config()
