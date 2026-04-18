#!/bin/bash
# Declarative manifest of what a correct audiobooks install looks like.
#
# Sourced by scripts/reconcile-filesystem.sh. Also sourceable by tests that
# want to assert the manifest matches reality (systemd/ directory, config.py
# defaults, etc.). Never execute side-effects here — pure data only.
#
# Two layouts are supported: system install under /opt/audiobooks and user
# install under ~/.local/share/audiobooks. The caller sets LIB_DIR + STATE_DIR
# + LOG_DIR + CONFIG_DIR before sourcing; the arrays below interpolate them.

# shellcheck disable=SC2034  # arrays are consumed by sourcing scripts

# ---------------------------------------------------------------------------
# Venvs that MUST exist after a successful install.
# Format: <path>|<purpose>
# ---------------------------------------------------------------------------
REQUIRED_VENVS=(
    "${LIB_DIR}/library/venv|main application venv (Flask, mutagen, etc.)"
    "${STATE_DIR}/audible-venv|isolated audible-cli venv (httpx conflict)"
)

# ---------------------------------------------------------------------------
# Paths that MUST NOT exist — historical drift locations. The reconciler
# deletes these if found. A user who genuinely needs one of these paths
# should override the canonical location in audiobooks.conf instead.
# ---------------------------------------------------------------------------
PHANTOM_PATHS=(
    "${LIB_DIR}/venv"                  # top-level venv (canonical is library/venv)
    "${LIB_DIR}/library/web-v2/covers" # pre-v7 cover dir (canonical is ${STATE_DIR}/covers)
    "${LIB_DIR}/library/covers"        # pre-v6 cover dir
    "${LIB_DIR}/.venv"                 # dev-machine convention
)

# ---------------------------------------------------------------------------
# Directories that MUST exist (created if missing, owner fixed if wrong).
# Format: <path>|<owner>:<group>|<mode>
# ---------------------------------------------------------------------------
REQUIRED_DIRS=(
    "${LIB_DIR}|audiobooks:audiobooks|0755"
    "${LIB_DIR}/library/data|audiobooks:audiobooks|0755"
    "${CONFIG_DIR}|root:audiobooks|0750"
    "${CONFIG_DIR}/scripts|audiobooks:audiobooks|0755"
    "${STATE_DIR}|audiobooks:audiobooks|0755"
    "${STATE_DIR}/db|audiobooks:audiobooks|0750"
    "${STATE_DIR}/data|audiobooks:audiobooks|0755"
    "${STATE_DIR}/covers|audiobooks:audiobooks|0755"
    "${STATE_DIR}/.run|audiobooks:audiobooks|0755"
    "${STATE_DIR}/.control|audiobooks:audiobooks|0755"
    "${LOG_DIR}|audiobooks:audiobooks|0755"
    "${LOG_DIR}/translate|audiobooks:audiobooks|0775"
)

# ---------------------------------------------------------------------------
# Systemd units that MUST be present under /etc/systemd/system/ (system
# install) or ~/.config/systemd/user/ (user install). Populated from the
# canonical systemd/ directory in the project at source time by the caller.
# ---------------------------------------------------------------------------
CANONICAL_UNITS=(
    "audiobook.target"
    "audiobook-api.service"
    "audiobook-proxy.service"
    "audiobook-redirect.service"
    "audiobook-converter.service"
    "audiobook-mover.service"
    "audiobook-scheduler.service"
    "audiobook-downloader.service"
    "audiobook-downloader.timer"
    "audiobook-enrichment.service"
    "audiobook-enrichment.timer"
    "audiobook-shutdown-saver.service"
    "audiobook-upgrade-helper.service"
    "audiobook-upgrade-helper.path"
    "audiobook-translate.service"
    "audiobook-translate-check.service"
    "audiobook-translate-check.timer"
    "audiobook-stream-translate.service"
    "audiobook-fleet-watchdog.service"
    "audiobook-fleet-watchdog.timer"
)

# ---------------------------------------------------------------------------
# Wrapper scripts that MUST be present in /usr/local/bin (system) or
# ~/.local/bin (user). These are the user-facing CLI entry points.
# ---------------------------------------------------------------------------
CANONICAL_WRAPPERS=(
    "audiobook-api"
    "audiobook-web"
    "audiobook-start"
    "audiobook-stop"
    "audiobook-status"
    "audiobook-enable"
    "audiobook-disable"
    "audiobook-scan"
    "audiobook-import"
    "audiobook-migrate"
    "audiobook-upgrade"
    "audiobook-user"
    "audiobook-help"
    "audiobook-config"
    "audiobook-download-monitor"
    "audiobook-purge-cache"
    "audiobook-save-staging"
    "audiobook-translations"
)

# ---------------------------------------------------------------------------
# Config keys that MUST NOT appear in audiobooks.conf with stale values.
# These all fall through to library/config.py defaults. If a user has set
# a NON-default value, it is preserved; only exact legacy defaults are
# stripped. Format: <key>|<legacy-value-glob>
#
# Keep this list aligned with library/config.py defaults. test_install_manifest
# asserts the mapping holds.
# ---------------------------------------------------------------------------
CONFIG_CANONICAL_DEFAULTS=(
    'AUDIOBOOKS_COVERS|*library/web-v2/covers'
    'AUDIOBOOKS_COVERS|*library/covers'
    'AUDIOBOOKS_DATABASE|*library/data/audiobooks.db'
    "AUDIOBOOKS_DATABASE|${STATE_DIR}/audiobooks.db"
    'AUDIOBOOKS_VENV|*library/venv'
    'AUDIOBOOKS_CERTS|*library/certs'
)
