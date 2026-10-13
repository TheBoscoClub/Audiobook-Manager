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
import re
from pathlib import Path
from typing import Callable, Optional, overload

# =============================================================================
# Configuration Loading
# =============================================================================


def _load_config_file(filepath: Path) -> dict[str, str]:
    """Load configuration from a shell-style config file."""
    config: dict[str, str] = {}
    if not filepath.exists():
        return config

    with open(filepath) as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            # ${VAR} references are NOT expanded here: expansion needs every
            # source (env, all conf files, computed defaults) plus cycle and
            # unknown-variable detection, so get_config() does it at lookup.
            config[key] = value

    return config


def _find_project_root() -> Optional[Path]:
    """Find project root by looking for marker files."""
    current = Path(__file__).parent
    markers = ["config.env", "lib/audiobook-config.sh", "library/config.py"]

    while current != current.parent:
        for marker in markers:
            if (current / marker).exists():
                return current
        # Also check if we're in library/ subdirectory
        if current.name == "library" and (current.parent / "lib").exists():
            return current.parent
        current = current.parent

    return None


# Load configuration from all sources
_config = {}

# Development launch (set by library/launch-v2.sh): never read the installed
# application's system or user config, so nothing falls through to production
# values for a key the project config.env does not set. Unset = unchanged.
_skip_installed_config = os.environ.get("AUDIOBOOKS_SKIP_SYSTEM_CONFIG", "").lower() in (
    "1",
    "true",
    "yes",
)

if not _skip_installed_config:
    # 1. System config
    _config.update(_load_config_file(Path("/etc/audiobooks/audiobooks.conf")))

    # 2. User config
    _user_config = Path.home() / ".config" / "audiobooks" / "audiobooks.conf"
    _config.update(_load_config_file(_user_config))

# 3. Legacy config.env (backwards compatibility)
_project_root = _find_project_root()
if _skip_installed_config and not (_project_root and (_project_root / "config.env").is_file()):
    # A dev launch always has a project config.env. Without one, skipping the
    # installed config would silently fall back to production code defaults.
    raise RuntimeError(
        "AUDIOBOOKS_SKIP_SYSTEM_CONFIG is set but no project config.env was found "
        "(copy config.env.example to config.env); refusing to fall back to "
        "production defaults"
    )
if _project_root:
    _config.update(_load_config_file(_project_root / "config.env"))


class ConfigError(ValueError):
    """A configuration value cannot be resolved to a usable setting."""


# Settings whose resolved value is a filesystem path. A '$' surviving in one
# of these is never legitimate: it means a reference was not expanded and a
# service would treat the literal text as a RELATIVE path under its
# WorkingDirectory (the /opt/audiobooks/${AUDIOBOOKS_DATA}/Library incident).
_PATH_KEYS = frozenset(
    {
        "AUDIOBOOKS_HOME",
        "AUDIOBOOKS_DATA",
        "AUDIOBOOKS_LIBRARY",
        "AUDIOBOOKS_SOURCES",
        "AUDIOBOOKS_SUPPLEMENTS",
        "AUDIOBOOKS_VAR_DIR",
        "AUDIOBOOKS_RUN_DIR",
        "AUDIOBOOKS_DATABASE",
        "AUDIOBOOKS_COVERS",
        "AUDIOBOOKS_STREAMING_AUDIO_DIR",
        "AUDIOBOOKS_STREAMING_SUBTITLES_DIR",
        "AUDIOBOOKS_CERTS",
        "AUDIOBOOKS_LOGS",
        "AUDIOBOOKS_WEBM_CACHE",
        "AUDIOBOOKS_STAGING",
        "AUDIOBOOKS_VENV",
        "AUDIOBOOKS_CONVERTER",
        "AUDIOBOOKS_CADDY_DIR",
        "DATA_DIR",
    }
)

# Values this module computes itself (auto-detected home, built-in defaults),
# registered as they resolve so ${AUDIOBOOKS_HOME} works when that setting is
# in neither the environment nor any conf file.
_resolved_defaults: dict[str, str] = {}

_REF_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def _raw_lookup(name: str) -> Optional[str]:
    """Unexpanded value of ``name``: environment, then conf files, then defaults."""
    if name in os.environ:
        return os.environ[name]
    if name in _config:
        return _config[name]
    return _resolved_defaults.get(name)


def _expand_references(
    value: str,
    key: str,
    _stack: tuple[str, ...] = (),
    _lookup: Optional[Callable[[str], Optional[str]]] = None,
) -> str:
    """Expand ${NAME} / $NAME in ``value``, the value of setting ``key``.

    Referenced settings are expanded first, so chains resolve in dependency
    order. An undefined reference or a cycle raises ConfigError naming the
    setting; nothing is ever silently replaced with an empty string.

    systemd's EnvironmentFile does not expand ${...}, so a service routinely
    receives the literal text of a conf-file template; this is where it is
    resolved.
    """
    lookup = _lookup or _raw_lookup
    if "$" not in value:
        return value
    stack = _stack + (key,)

    def sub(match: "re.Match[str]") -> str:
        name = match.group(1) or match.group(2)
        if name in stack:
            raise ConfigError(f"{key}: circular variable reference ({' -> '.join((*stack, name))})")
        raw = lookup(name)
        if raw is None:
            raise ConfigError(f"{key}: references undefined variable {name} (value: {value!r})")
        return _expand_references(raw, name, stack, lookup)

    return _REF_RE.sub(sub, value)


def _expand_vars(value: Optional[str], key: str = "<value>") -> Optional[str]:
    """Expand references in ``value``; None passes through."""
    if value is None:
        return value
    return _expand_references(value, key)


def _guard_path_value(key: str, value: str) -> str:
    """Refuse a path setting that still contains '$' after expansion."""
    if "$" in value:
        raise ConfigError(
            f"{key}={value!r} still contains an unexpanded '$' after reference "
            "expansion; refusing to use it as a path"
        )
    return value


@overload
def get_config(key: str) -> Optional[str]: ...


@overload
def get_config(key: str, default: str) -> str: ...


def get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get configuration value with environment override."""
    value = os.environ.get(key, _config.get(key, default))
    value = _expand_vars(value, key)
    if value is not None and key in _PATH_KEYS:
        _guard_path_value(key, value)
    return value


# =============================================================================
# Core Paths
# =============================================================================

# Auto-detect AUDIOBOOKS_HOME if not set
_detected_home = str(_project_root) if _project_root else None
AUDIOBOOKS_HOME = Path(get_config("AUDIOBOOKS_HOME", _detected_home or "/opt/audiobooks"))
_resolved_defaults["AUDIOBOOKS_HOME"] = str(AUDIOBOOKS_HOME)

# Core data directory
AUDIOBOOKS_DATA = Path(get_config("AUDIOBOOKS_DATA", "/srv/audiobooks"))
_resolved_defaults["AUDIOBOOKS_DATA"] = str(AUDIOBOOKS_DATA)

# Data subdirectories
AUDIOBOOKS_LIBRARY = Path(get_config("AUDIOBOOKS_LIBRARY", str(AUDIOBOOKS_DATA / "Library")))
AUDIOBOOKS_SOURCES = Path(get_config("AUDIOBOOKS_SOURCES", str(AUDIOBOOKS_DATA / "Sources")))
AUDIOBOOKS_SUPPLEMENTS = Path(
    get_config("AUDIOBOOKS_SUPPLEMENTS", str(AUDIOBOOKS_DATA / "Supplements"))
)

# Application paths
# Database should be in a data directory, NOT in the code directory
# AUDIOBOOKS_VAR_DIR is the persistent state directory (default: /var/lib/audiobooks)
_var_dir = get_config("AUDIOBOOKS_VAR_DIR", "/var/lib/audiobooks")
_resolved_defaults["AUDIOBOOKS_VAR_DIR"] = _var_dir
AUDIOBOOKS_DATABASE = Path(get_config("AUDIOBOOKS_DATABASE", f"{_var_dir}/db/audiobooks.db"))
AUDIOBOOKS_COVERS = Path(get_config("AUDIOBOOKS_COVERS", f"{_var_dir}/covers"))
# Streaming translation audio buffer — short-lived per-segment opus files
# staged for the streaming pipeline. MUST match lib/audiobook-config.sh.
AUDIOBOOKS_STREAMING_AUDIO_DIR = Path(
    get_config("AUDIOBOOKS_STREAMING_AUDIO_DIR", f"{_var_dir}/streaming-audio")
)
# Streaming translation subtitles cache — consolidated per-chapter VTT files
# from the streaming pipeline. Lives under AUDIOBOOKS_VAR_DIR (writable runtime
# state) rather than the read-only install tree at /opt/audiobooks/library.
# MUST match lib/audiobook-config.sh.
AUDIOBOOKS_STREAMING_SUBTITLES_DIR = Path(
    get_config("AUDIOBOOKS_STREAMING_SUBTITLES_DIR", f"{_var_dir}/streaming-subtitles")
)
AUDIOBOOKS_CERTS = Path(get_config("AUDIOBOOKS_CERTS", str(AUDIOBOOKS_HOME / "library" / "certs")))
AUDIOBOOKS_LOGS = Path(get_config("AUDIOBOOKS_LOGS", str(AUDIOBOOKS_DATA / "logs")))
AUDIOBOOKS_WEBM_CACHE = Path(
    get_config("AUDIOBOOKS_WEBM_CACHE", str(AUDIOBOOKS_DATA / ".webm-cache"))
)
AUDIOBOOKS_STAGING = Path(
    get_config("AUDIOBOOKS_STAGING", "/tmp/audiobook-staging")  # noqa: S108 — default only; users override via audiobooks.conf; service creates dir 0700  # nosec B108 — tmp path — config default only; production overrides via audiobooks.conf; service creates dir 0700
)  # nosec B108
AUDIOBOOKS_VENV = Path(get_config("AUDIOBOOKS_VENV", str(AUDIOBOOKS_HOME / "library" / "venv")))
AUDIOBOOKS_CONVERTER = Path(
    get_config("AUDIOBOOKS_CONVERTER", str(AUDIOBOOKS_HOME / "converter" / "AAXtoMP3"))
)

# Server settings
AUDIOBOOKS_API_PORT = int(get_config("AUDIOBOOKS_API_PORT", "5001"))
AUDIOBOOKS_WEB_PORT = int(
    get_config("AUDIOBOOKS_WEB_PORT", "8443")
)  # Changed from 8090 to 8443 (HTTPS)
AUDIOBOOKS_HTTP_REDIRECT_PORT = int(
    os.environ.get("HTTP_REDIRECT_PORT", str(get_config("AUDIOBOOKS_HTTP_REDIRECT_PORT", "8080")))
)  # Default 8080 (matches audiobook-config.sh and .env.example)
# Also checks Docker legacy env var HTTP_REDIRECT_PORT
AUDIOBOOKS_BIND_ADDRESS = get_config(
    "AUDIOBOOKS_BIND_ADDRESS",
    "0.0.0.0",  # noqa: S104 — configurable default; fronted by Caddy/HTTPS proxy, not exposed directly
)  # nosec B104
AUDIOBOOKS_HTTPS_ENABLED = get_config("AUDIOBOOKS_HTTPS_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
_http_redirect_enabled_raw = os.environ.get(
    "HTTP_REDIRECT_ENABLED", get_config("AUDIOBOOKS_HTTP_REDIRECT_ENABLED", "true")
)
AUDIOBOOKS_HTTP_REDIRECT_ENABLED = _http_redirect_enabled_raw.lower() in ("true", "1", "yes")
# AUDIOBOOKS_USE_WAITRESS removed in v7.2 — migrated to Gunicorn+gevent

# =============================================================================
# Legacy Aliases (backwards compatibility)
# =============================================================================

# Check for legacy environment variables first (Docker compatibility)
# Then fall back to new naming convention
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", str(AUDIOBOOKS_HOME)))
LIBRARY_DIR = PROJECT_DIR / "library" if PROJECT_DIR else Path(".")
AUDIOBOOK_DIR = Path(os.environ.get("AUDIOBOOK_DIR", str(AUDIOBOOKS_LIBRARY)))
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", str(AUDIOBOOKS_DATABASE)))
COVER_DIR = Path(os.environ.get("COVER_DIR", str(AUDIOBOOKS_COVERS)))
# DATA_DIR: uses get_config() to read from config files, not just env vars
DATA_DIR = Path(
    get_config("DATA_DIR", str(PROJECT_DIR / "library" / "data" if PROJECT_DIR else "."))
)
SOURCES_DIR = AUDIOBOOKS_SOURCES
SUPPLEMENTS_DIR = Path(os.environ.get("SUPPLEMENTS_DIR", str(AUDIOBOOKS_SUPPLEMENTS)))
OPUS_DIR = AUDIOBOOK_DIR  # Points to same as AUDIOBOOK_DIR
CONVERTED_DIR = AUDIOBOOK_DIR  # Points to same as AUDIOBOOK_DIR
WEB_PORT = int(os.environ.get("WEB_PORT", str(AUDIOBOOKS_WEB_PORT)))
API_PORT = int(os.environ.get("API_PORT", str(AUDIOBOOKS_API_PORT)))
HTTP_REDIRECT_PORT = int(os.environ.get("HTTP_REDIRECT_PORT", str(AUDIOBOOKS_HTTP_REDIRECT_PORT)))
HTTP_REDIRECT_ENABLED = os.environ.get(
    "HTTP_REDIRECT_ENABLED", str(AUDIOBOOKS_HTTP_REDIRECT_ENABLED).lower()
).lower() in ("true", "1", "yes")

# Final guard: every resolved path constant (including the legacy aliases that
# read os.environ directly) must be free of unexpanded references.
for _name, _val in (
    ("AUDIOBOOKS_HOME", AUDIOBOOKS_HOME),
    ("AUDIOBOOKS_DATA", AUDIOBOOKS_DATA),
    ("AUDIOBOOKS_LIBRARY", AUDIOBOOKS_LIBRARY),
    ("AUDIOBOOKS_SOURCES", AUDIOBOOKS_SOURCES),
    ("AUDIOBOOKS_SUPPLEMENTS", AUDIOBOOKS_SUPPLEMENTS),
    ("AUDIOBOOKS_DATABASE", AUDIOBOOKS_DATABASE),
    ("AUDIOBOOKS_COVERS", AUDIOBOOKS_COVERS),
    ("AUDIOBOOKS_STREAMING_AUDIO_DIR", AUDIOBOOKS_STREAMING_AUDIO_DIR),
    ("AUDIOBOOKS_STREAMING_SUBTITLES_DIR", AUDIOBOOKS_STREAMING_SUBTITLES_DIR),
    ("AUDIOBOOKS_CERTS", AUDIOBOOKS_CERTS),
    ("AUDIOBOOKS_LOGS", AUDIOBOOKS_LOGS),
    ("AUDIOBOOKS_WEBM_CACHE", AUDIOBOOKS_WEBM_CACHE),
    ("AUDIOBOOKS_STAGING", AUDIOBOOKS_STAGING),
    ("AUDIOBOOKS_VENV", AUDIOBOOKS_VENV),
    ("AUDIOBOOKS_CONVERTER", AUDIOBOOKS_CONVERTER),
    ("PROJECT_DIR", PROJECT_DIR),
    ("AUDIOBOOK_DIR", AUDIOBOOK_DIR),
    ("DATABASE_PATH", DATABASE_PATH),
    ("COVER_DIR", COVER_DIR),
    ("DATA_DIR", DATA_DIR),
    ("SUPPLEMENTS_DIR", SUPPLEMENTS_DIR),
):
    _guard_path_value(_name, str(_val))
del _name, _val

# =============================================================================
# Utility Functions
# =============================================================================


def require_audiobooks_user() -> None:
    """Abort with a usage message if not running as the audiobooks user.

    Every script that reads/writes the audiobook DB, reads the operator
    config, or spawns worker subprocesses MUST run as the audiobooks
    service account. The DB and /etc/audiobooks/audiobooks.conf are
    0640 audiobooks:audiobooks by design — running as root or an
    interactive user bypasses the permission model the systemd units
    rely on.

    Bypass: set ``AUDIOBOOKS_SKIP_USER_GATE=1`` in the environment.
    Intended for pytest runs (conftest sets it universally) and
    developer sandboxes. Production scripts never set this.

    Mirrors require_audiobooks_user in lib/audiobook-config.sh.
    """
    import os
    import pwd
    import sys

    if os.environ.get("AUDIOBOOKS_SKIP_USER_GATE") == "1":
        return

    current = pwd.getpwuid(os.getuid()).pw_name
    if current == "audiobooks":
        return

    script_name = os.path.basename(sys.argv[0]) if sys.argv else "<script>"
    rest_args = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    print(
        f"error: {script_name} must run as the audiobooks user.\n"
        f"\n"
        f"  current:  {current} (uid {os.getuid()})\n"
        f"  required: audiobooks\n"
        f"\n"
        f"Re-invoke with:\n"
        f"\n"
        f"  sudo -u audiobooks {script_name} {rest_args}\n"
        f"\n"
        f"(The audiobook database and /etc/audiobooks/audiobooks.conf are owned\n"
        f"audiobooks:audiobooks by design. Running as anyone else bypasses the\n"
        f"permission model the systemd units rely on.)",
        file=sys.stderr,
    )
    sys.exit(1)


def print_config() -> None:
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
    print(f"AUDIOBOOKS_STREAMING_AUDIO_DIR:     {AUDIOBOOKS_STREAMING_AUDIO_DIR}")
    print(f"AUDIOBOOKS_STREAMING_SUBTITLES_DIR: {AUDIOBOOKS_STREAMING_SUBTITLES_DIR}")
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
    print("WSGI_SERVER:            Gunicorn+geventwebsocket")
    print("=" * 50)


def check_dirs() -> bool:
    """Verify required directories exist. Returns True if all exist."""
    required = [AUDIOBOOKS_LIBRARY, AUDIOBOOKS_DATABASE.parent]
    missing = []
    for d in required:
        if not d.exists():
            missing.append(str(d))
    if missing:
        print("Warning: Missing directories:", file=__import__("sys").stderr)
        for m in missing:
            print(f"  - {m}", file=__import__("sys").stderr)
        return False
    return True


if __name__ == "__main__":
    print_config()
