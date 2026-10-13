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
# Obsolete files the installer NO LONGER ships, left in an install root by an
# old full-tree copy (Audiobook-Manager-soo). EXPLICIT list, paths RELATIVE to
# ${LIB_DIR} — never "delete whatever is not shipped". The reconciler
# (_reconcile_obsolete_root_paths) removes exactly these in enforce mode and
# only reports them in report mode; it refuses to run unless ${LIB_DIR} holds
# VERSION + install-manifest.json, rejects absolute/".." entries, and never
# follows a symlinked parent. Add a path here ONLY if the installer provably
# does not ship it.
#
# Deliberately NOT listed: library/certs — install.sh generates the TLS pair
# there and AUDIOBOOKS_CERTS defaults to it (CONFIG_CANONICAL_DEFAULTS below).
# scripts/purge-users.sh and scripts/install-hooks.sh are dev-machine tools
# skipped by the install/upgrade copy loops; an installed copy is a leftover.
# ---------------------------------------------------------------------------
OBSOLETE_ROOT_PATHS=(
    "upgrade.sh" # stale root copy; the live one is scripts/upgrade.sh
    "install.sh"
    "bootstrap-install.sh"
    "create-release.sh"
    "launch.sh"
    "README.md"
    "Dockerfile"
    "docker-compose.yml"
    ".python-version"
    ".coverage"
    "test-output.log"
    ".mypy_cache"
    ".ruff_cache"
    "library/.mypy_cache"
    "scripts/purge-users.sh"   # dev tool, deliberately never copied by install/upgrade
    "scripts/install-hooks.sh" # dev tool (git hooks), deliberately never copied
    # Screenshots committed to the repo root before 6e9abc63 (2026-02-21)
    "codecov-page.png"
    "css-fix-desktop-1280x720.png"
    "css-fix-landscape-812x375.png"
    "css-fix-mobile-375x812.png"
    "css-fix-small-phone-320x568.png"
    "css-fix-tablet-768x1024.png"
    "vm-e2e-main-library.png"
    "vm-e2e-my-library-tab.png"
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
    "${STATE_DIR}/streaming-audio|audiobooks:audiobooks|0750"
    "${STATE_DIR}/streaming-subtitles|audiobooks:audiobooks|0755"
    "${LOG_DIR}|audiobooks:audiobooks|0755"
    "${LOG_DIR}/translate|audiobooks:audiobooks|0775"
)

# ---------------------------------------------------------------------------
# Systemd units that MUST be present under /etc/systemd/system/ (system
# install) or ~/.config/systemd/user/ (user install). Populated from the
# canonical systemd/ directory in the project at source time by the caller.
#
# Deliberately NOT listed: audiobook-api.service.d/derive-secrets.conf — a
# host-conditional drop-in that install.sh/upgrade.sh install only when
# /usr/bin/derive-service-secret exists (operator tooling). Listing it here
# would make the reconciler flag hosts without the tool as drifted.
# Template: systemd/audiobook-api-derive-secrets.conf.example
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
    "audiobook-stream-translate.service"
    "audiobook-translation-monitor-live.service"
    "audiobook-translation-monitor-live.timer"
    "audiobook-translation-monitor-sampler.service"
    "audiobook-translation-monitor-sampler.timer"
)

# ---------------------------------------------------------------------------
# Worker scripts under scripts/ that systemd units invoke (directly or via a
# wrapper). These MUST be copied into /opt/audiobooks/scripts/ by install.sh
# and upgrade.sh. Test guard: library/tests/test_stream_translate_wiring.py
# greps this file for each worker name to catch orphan-script regressions
# (see 8.3.1 stream-translate-worker.py incident).
#
# Format: <relative-path-in-scripts-dir>|<invoking-unit>
# ---------------------------------------------------------------------------
CANONICAL_WORKERS=(
    "stream-translate-worker.py|audiobook-stream-translate.service (via stream-translate-daemon.sh)"
    "stream-translate-daemon.sh|audiobook-stream-translate.service"
    "translation-monitor-live.py|audiobook-translation-monitor-live.service (timer-driven, every 30s)"
    "translation-monitor-sampler.py|audiobook-translation-monitor-sampler.service (timer-driven, every 5min)"
)

# ---------------------------------------------------------------------------
# Standalone operator CLI tools — copied by install.sh / upgrade.sh into
# ${LIB_DIR}/scripts/ but NOT wired into the systemd service graph. These are
# manually invoked by the operator (admin) for backfills, audits, repairs.
# Each script carries a `# /test:wiring-exception:` comment explaining why
# it is not service-graph wired. Test guard: library/tests/test_install_manifest.py
# (or successor) greps this list to confirm presence and copy coverage.
# ---------------------------------------------------------------------------
CANONICAL_OPERATOR_TOOLS=(
    "batch-translate.py|operator-run: standalone STT->translate->TTS pipeline runner"
    "email-report.py|operator-run: emails translation verification report"
    "verify-translations.py|operator-run: audits completed translations for proof"
    "embed-cover-art.py|operator-run: embeds cover art into Opus files (also exposed via audiobook-embed-covers wrapper)"
    "sampler-reconcile.py|operator-run: backfill sampler_jobs rows for missing (book,locale) pairs (also exposed via sampler-reconcile wrapper)"
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

# ---------------------------------------------------------------------------
# audiobooks.conf ownership and mode. The file may carry inline credentials
# (SMTP_PASS and friends), so it is never world-readable. System install:
# root:audiobooks 0640 -- systemd reads EnvironmentFile= as root before the
# unit drops to User=audiobooks, and the audiobooks group lets the services'
# own config readers (library/config.py, lib/audiobook-config.sh) open it.
# User install: the installing user, 0600. The reconciler enforces these and
# the backups it makes of the file are never wider.
# ---------------------------------------------------------------------------
SYSTEM_CONFIG_OWNER="root:audiobooks"
SYSTEM_CONFIG_MODE="0640"
USER_CONFIG_MODE="0600"

# ---------------------------------------------------------------------------
# Path-valued config keys. systemd's EnvironmentFile= does not expand ${VAR},
# so a line like AUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}/Library" reaches a
# service as literal text, which WorkingDirectory-relative code resolves as a
# relative path. The app now expands these itself (library/config.py,
# lib/audiobook-config.sh); the reconciler reports and, in enforce mode,
# rewrites such lines in the installed config to concrete values. Only these
# keys are touched — any other value may legitimately contain a '$'.
# MUST equal _AB_PATH_VARS in lib/audiobook-config.sh (test_install_manifest_reconciler
# asserts it) and be a superset of _PATH_KEYS in library/config.py.
# ---------------------------------------------------------------------------
CONFIG_PATH_KEYS=(
    AUDIOBOOKS_HOME AUDIOBOOKS_DATA AUDIOBOOKS_LIBRARY AUDIOBOOKS_SOURCES
    AUDIOBOOKS_SUPPLEMENTS AUDIOBOOKS_LOGS AUDIOBOOKS_DATABASE AUDIOBOOKS_COVERS
    AUDIOBOOKS_CERTS AUDIOBOOKS_VENV AUDIOBOOKS_CADDY_DIR AUDIOBOOKS_CONVERTER
    AUDIOBOOKS_STAGING AUDIOBOOKS_WEBM_CACHE AUDIOBOOKS_RUN_DIR AUDIOBOOKS_VAR_DIR
    AUDIOBOOKS_STREAMING_AUDIO_DIR AUDIOBOOKS_STREAMING_SUBTITLES_DIR DATA_DIR
    AUDIOBOOKS_TRIGGERS AUDIOBOOKS_DOWNLOADER_LOCK AUDIOBOOKS_AUDIBLE_VENV
    AUDIOBOOKS_AUDIBLE_CMD AUDIOBOOKS_INDEX AUDIOBOOKS_PROJECT_DIR AUTH_DATABASE
    AUTH_DB_PATH AUTH_KEY_FILE SMTP_PASS_FILE CLOUDFLARE_PURGE_TOKEN_FILE
    AUDIOBOOKS_RUNPOD_API_KEY_FILE AUDIOBOOKS_WHISPER_GPU_TOKEN_FILE
)

# ---------------------------------------------------------------------------
# Optional credential files for the *_FILE pointer pattern (v8.4.0.0+).
# SINGLE CANONICAL DEFINITION SITE (Audiobook-Manager-be6): install.sh,
# upgrade.sh, and scripts/reconcile-filesystem.sh all derive their stub
# creation / permission reconciliation from this array — never hardcode the
# stub names anywhere else. The listed file is reconciled for owner/mode only
# if it exists; absence is not drift.
#
# 2026-08-26 (Audiobook-Manager-ac2/nsz): this list previously held four EMPTY
# stubs and did not hold the one real credential. All four are now obsolete —
# mail submits credential-less to the local relay (9nu), RunPod is
# decommissioned, and the Cloudflare purge token is
# DERIVED at service start to /run/audiobooks/ by derive-service-secret rather
# than read from this directory. Empty placeholders are worse than absent: they
# were the entire content of the reconciler's per-deploy drift report, and a
# credential path that exists invites someone to hand-populate it, which is the
# rotation-drift vector ac2 exists to remove.
# Format: <path>|<owner>:<group>|<mode>|<env-var-name>
# ---------------------------------------------------------------------------
OPTIONAL_CREDENTIAL_FILES=(
    "${CONFIG_DIR}/auth.key|audiobooks:audiobooks|0600|AUTH_KEY_FILE"
)
