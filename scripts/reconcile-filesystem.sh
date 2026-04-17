#!/bin/bash
# Idempotent filesystem reconciler — brings an audiobooks install into
# agreement with scripts/install-manifest.sh.
#
# Runs at the end of install.sh and upgrade.sh. Report-only by default in
# this release (RECONCILE_MODE=report). Set RECONCILE_MODE=enforce to delete
# phantoms, create missing dirs/venvs, etc. The flip to enforce-by-default
# will happen in a subsequent release after users have seen the reports.
#
# Required environment (set by caller):
#   LIB_DIR      — application directory (/opt/audiobooks or ~/.local/share/audiobooks)
#   STATE_DIR    — state directory (/var/lib/audiobooks or ~/.local/state/audiobooks)
#   LOG_DIR      — log directory
#   CONFIG_DIR   — config directory
#   CONF_FILE    — path to audiobooks.conf
#   USE_SUDO     — "sudo" for system install, "" for user install
#   PROJECT_DIR  — absolute path to the project root (so we can source the manifest)
#
# Optional:
#   RECONCILE_MODE — report (default) | enforce
#   DRY_RUN        — true | false (default false)
#   SYSTEMD_DIR    — /etc/systemd/system or ~/.config/systemd/user
#   BIN_DIR        — /usr/local/bin or ~/.local/bin

# shellcheck disable=SC2154  # vars come from caller environment

set -u

RECONCILE_MODE="${RECONCILE_MODE:-report}"
DRY_RUN="${DRY_RUN:-false}"

_issues=0
_fixed=0

_log() { echo "  $*"; }
_issue() {
    echo "  [drift] $*"
    _issues=$((_issues + 1))
}
_fix() {
    echo "  [fix]   $*"
    _fixed=$((_fixed + 1))
}

_should_act() {
    [[ "$RECONCILE_MODE" == "enforce" && "$DRY_RUN" != "true" ]]
}

# ---------------------------------------------------------------------------
# Step 1 — delete phantom paths
# ---------------------------------------------------------------------------
_reconcile_phantoms() {
    local path
    for path in "${PHANTOM_PATHS[@]}"; do
        [[ -e "$path" || -L "$path" ]] || continue
        _issue "phantom path present: $path"
        if _should_act; then
            $USE_SUDO rm -rf "$path"
            _fix "removed phantom: $path"
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 2 — ensure required directories exist with correct owner/mode
# ---------------------------------------------------------------------------
_reconcile_dirs() {
    local entry path owner mode
    for entry in "${REQUIRED_DIRS[@]}"; do
        IFS='|' read -r path owner mode <<<"$entry"
        if [[ ! -d "$path" ]]; then
            _issue "missing directory: $path"
            if _should_act; then
                $USE_SUDO mkdir -p "$path"
                $USE_SUDO chown "$owner" "$path" 2>/dev/null || true
                $USE_SUDO chmod "$mode" "$path" 2>/dev/null || true
                _fix "created: $path ($owner $mode)"
            fi
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 3 — ensure required venvs exist (create stubs if enforcing)
# Venv recreation is expensive, so in enforce mode we only report; install.sh
# and upgrade.sh handle actual venv creation via their existing code paths.
# ---------------------------------------------------------------------------
_reconcile_venvs() {
    local entry path purpose
    for entry in "${REQUIRED_VENVS[@]}"; do
        IFS='|' read -r path purpose <<<"$entry"
        if [[ ! -x "${path}/bin/python" ]]; then
            _issue "missing venv: $path ($purpose)"
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 4 — audit installed systemd units vs canonical list
# ---------------------------------------------------------------------------
_reconcile_units() {
    local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
    local unit
    for unit in "${CANONICAL_UNITS[@]}"; do
        if [[ ! -f "${systemd_dir}/${unit}" ]]; then
            _issue "missing systemd unit: ${systemd_dir}/${unit}"
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 5 — audit wrapper scripts in BIN_DIR
# ---------------------------------------------------------------------------
_reconcile_wrappers() {
    local bin_dir="${BIN_DIR:-/usr/local/bin}"
    local wrapper
    for wrapper in "${CANONICAL_WRAPPERS[@]}"; do
        if [[ ! -x "${bin_dir}/${wrapper}" ]]; then
            _issue "missing wrapper: ${bin_dir}/${wrapper}"
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 6 — strip legacy config overrides
# Only removes keys whose value matches a documented legacy glob. User
# customizations (non-legacy paths) are preserved untouched.
# ---------------------------------------------------------------------------
_reconcile_config() {
    [[ -f "$CONF_FILE" ]] || return 0

    local entry key legacy_glob current
    for entry in "${CONFIG_CANONICAL_DEFAULTS[@]}"; do
        IFS='|' read -r key legacy_glob <<<"$entry"
        current=$(grep -oP "^${key}=\K.*" "$CONF_FILE" 2>/dev/null | tr -d '"' || true)
        [[ -z "$current" ]] && continue

        # shellcheck disable=SC2254  # glob match on legacy value is intentional
        case "$current" in
            $legacy_glob)
                _issue "legacy ${key}=${current} in ${CONF_FILE}"
                if _should_act; then
                    $USE_SUDO sed -i.bak-reconcile "/^${key}=/d" "$CONF_FILE"
                    _fix "stripped ${key} from ${CONF_FILE}"
                fi
                ;;
        esac
    done
}

# ---------------------------------------------------------------------------
# Step 7 — clean __pycache__ directories under LIB_DIR (stale bytecode
# is a common source of "fix not taking effect" bugs after upgrades)
# ---------------------------------------------------------------------------
_reconcile_pycache() {
    [[ -d "$LIB_DIR" ]] || return 0
    local count
    count=$(find "$LIB_DIR" -type d -name __pycache__ 2>/dev/null | wc -l)
    if ((count > 0)); then
        _log "found ${count} __pycache__ directories under ${LIB_DIR}"
        if _should_act; then
            # shellcheck disable=SC2086
            find "$LIB_DIR" -type d -name __pycache__ -exec $USE_SUDO rm -rf {} + 2>/dev/null || true
            _fix "cleaned ${count} __pycache__ directories"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Step 8 — report
# ---------------------------------------------------------------------------
_reconcile_report() {
    echo
    echo "=== Filesystem reconciliation report ==="
    echo "  Mode:   ${RECONCILE_MODE}$([[ "$DRY_RUN" == "true" ]] && echo " (dry-run)")"
    echo "  Drift:  ${_issues} issue(s)"
    echo "  Fixed:  ${_fixed}"
    if ((_issues > 0 && _fixed == 0)); then
        echo
        echo "  Run with RECONCILE_MODE=enforce to fix, or inspect manually."
    fi
    echo
}

reconcile_filesystem() {
    local manifest="${PROJECT_DIR}/scripts/install-manifest.sh"
    if [[ ! -f "$manifest" ]]; then
        echo "  [warn] install manifest not found at $manifest — skipping reconciliation"
        return 0
    fi

    # shellcheck source=install-manifest.sh
    source "$manifest"

    echo
    echo "=== Reconciling filesystem against install manifest ==="

    _reconcile_phantoms
    _reconcile_dirs
    _reconcile_venvs
    _reconcile_units
    _reconcile_wrappers
    _reconcile_config
    _reconcile_pycache
    _reconcile_report
}

# Allow direct invocation for debugging: bash reconcile-filesystem.sh
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    : "${PROJECT_DIR:?PROJECT_DIR must be set}"
    : "${LIB_DIR:?LIB_DIR must be set}"
    : "${STATE_DIR:?STATE_DIR must be set}"
    : "${LOG_DIR:?LOG_DIR must be set}"
    : "${CONFIG_DIR:?CONFIG_DIR must be set}"
    : "${CONF_FILE:?CONF_FILE must be set}"
    : "${USE_SUDO:=}"
    reconcile_filesystem
fi
