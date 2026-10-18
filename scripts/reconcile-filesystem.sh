#!/bin/bash
# Idempotent filesystem reconciler — brings an audiobooks install into
# agreement with scripts/install-manifest.sh.
#
# Runs at the end of install.sh and upgrade.sh. Enforce by default when
# invoked from install.sh/upgrade.sh — the items actually acted on
# (PHANTOM_PATHS, OBSOLETE_ROOT_PATHS, legacy config keys listed in
# CONFIG_CANONICAL_DEFAULTS, ${VAR} references in path-valued config keys,
# stale __pycache__) are explicitly marked as obsolete in the manifest.
# The script itself defaults to `report` when invoked directly for debugging
# so ad-hoc runs don't surprise; callers override to `enforce`.
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
            $USE_SUDO rm -rf "${path:?}"
            _fix "removed phantom: $path"
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 1b — delete obsolete install-root files (OBSOLETE_ROOT_PATHS)
# Explicit relative list from the manifest; never discovery-based. Safety:
#   * refuses unless LIB_DIR holds VERSION + install-manifest.json
#   * rejects empty, absolute and ".." entries
#   * skips an entry whose parent chain contains a symlink (would leave LIB_DIR)
#   * a symlink entry is unlinked, never followed; directories use rm -rf
#     --one-file-system, which does not follow links either
# ---------------------------------------------------------------------------
_reconcile_obsolete_root_paths() {
    [[ "${#OBSOLETE_ROOT_PATHS[@]}" -gt 0 ]] || return 0

    # Refuse unless LIB_DIR is positively an INSTALL. VERSION + install-manifest.json
    # are also tracked in the git repo, so they alone cannot tell the two apart.
    local lib_real project_real
    if [[ -z "${LIB_DIR:-}" || "${LIB_DIR%/}" == "" ]]; then
        _log "obsolete-path purge skipped: LIB_DIR is empty or /"
        return 0
    fi
    lib_real="$(realpath -e -- "$LIB_DIR" 2>/dev/null)" || {
        _log "obsolete-path purge skipped: cannot resolve LIB_DIR=${LIB_DIR}"
        return 0
    }
    if [[ "$lib_real" == "/" ]]; then
        _log "obsolete-path purge skipped: LIB_DIR resolves to /"
        return 0
    fi
    if [[ -e "${lib_real}/.git" ]]; then
        _log "obsolete-path purge skipped: ${lib_real} is a git checkout, not an install"
        return 0
    fi
    project_real="$(realpath -e -- "${PROJECT_DIR:-}" 2>/dev/null || true)"
    if [[ -n "$project_real" && "$project_real" == "$lib_real" ]]; then
        _log "obsolete-path purge skipped: LIB_DIR is the project source tree"
        return 0
    fi
    if [[ ! -f "${lib_real}/VERSION" || ! -f "${lib_real}/install-manifest.json" ]]; then
        _log "obsolete-path purge skipped: ${LIB_DIR} lacks VERSION + install-manifest.json (not an audiobooks install)"
        return 0
    fi

    local rel parent comp cur unsafe full parent_real
    local -a parts
    for rel in "${OBSOLETE_ROOT_PATHS[@]}"; do
        if [[ -z "$rel" || "$rel" == /* || "/${rel}/" == */../* || "/${rel}/" == */./* ]]; then
            _issue "invalid OBSOLETE_ROOT_PATHS entry (empty/absolute/./..): '${rel}'"
            continue
        fi
        # Walk the parent chain; any symlink means the path may leave LIB_DIR.
        unsafe=""
        cur="${lib_real}"
        parent="$(dirname "$rel")"
        if [[ "$parent" != "." ]]; then
            IFS=/ read -ra parts <<<"$parent"
            for comp in "${parts[@]}"; do
                cur="${cur}/${comp}"
                if [[ -L "$cur" ]]; then
                    unsafe="$cur"
                    break
                fi
            done
        fi
        if [[ -n "$unsafe" ]]; then
            _issue "obsolete path skipped, symlinked parent ${unsafe}: ${rel}"
            continue
        fi
        full="${lib_real}/${rel}"
        [[ -e "$full" || -L "$full" ]] || continue
        _issue "obsolete install-root path present: ${full}"
        if _should_act; then
            # TOCTOU re-check, immediately before the delete: the parent must STILL
            # resolve inside LIB_DIR (a symlink swapped in after the walk above is
            # caught here). The window is not zero — closing it needs openat-style
            # deletion — but the purge only ever runs as the install owner/root on
            # a stopped service. The PHANTOM_PATHS purge above shares this weakness
            # and is left unchanged.
            parent_real="$(realpath -e -- "$(dirname "$full")" 2>/dev/null || true)"
            if [[ -z "$parent_real" || ("$parent_real" != "$lib_real" && "$parent_real" != "$lib_real"/*) ]]; then
                _issue "obsolete path skipped, parent no longer inside install dir: ${full}"
                continue
            fi
            if $USE_SUDO rm -rf --one-file-system -- "${full:?}"; then
                _fix "removed obsolete: ${full}"
            else
                _issue "could not remove obsolete path: ${full}"
            fi
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
# Step 5b — audiobooks.conf owner/group/mode, and private backups of it
# The file can hold inline credentials. System install: SYSTEM_CONFIG_OWNER /
# SYSTEM_CONFIG_MODE (root:audiobooks 0640). User install (config dir under
# $HOME): the invoking user, USER_CONFIG_MODE (0600). CONF_FILE_OWNER and
# CONF_FILE_MODE override both -- used by the regression suite only.
# Report mode reports; enforce mode fixes. Numeric mode compare (_norm_mode).
# ---------------------------------------------------------------------------
_conf_want() {
    if [[ -n "${CONF_FILE_OWNER:-}" || -n "${CONF_FILE_MODE:-}" ]]; then
        _WANT_OWNER="${CONF_FILE_OWNER:-$SYSTEM_CONFIG_OWNER}"
        _WANT_MODE="${CONF_FILE_MODE:-$SYSTEM_CONFIG_MODE}"
    elif [[ -n "${HOME:-}" && "${CONFIG_DIR:-}" == "${HOME}"/* ]]; then
        _WANT_OWNER="$(id -un):$(id -gn)"
        _WANT_MODE="$USER_CONFIG_MODE"
    else
        _WANT_OWNER="$SYSTEM_CONFIG_OWNER"
        _WANT_MODE="$SYSTEM_CONFIG_MODE"
    fi
}

# Copy CONF_FILE to "${CONF_FILE}$1" without ever exposing it: created 0600
# under umask 077, then given the wanted owner/mode (never wider than the
# config itself). Sets _BAK. Returns 1 if the copy fails.
_conf_backup() {
    _conf_want
    _BAK="${CONF_FILE}$1"
    (umask 077 && $USE_SUDO cp -- "$CONF_FILE" "$_BAK") || return 1
    $USE_SUDO chown "$_WANT_OWNER" "$_BAK" 2>/dev/null || true
    $USE_SUDO chmod "$_WANT_MODE" "$_BAK"
}

# Check one file against _WANT_OWNER/_WANT_MODE; fix it in enforce mode.
_perm_check_one() {
    local path="$1" label="$2" cur_owner cur_mode
    cur_owner="$($USE_SUDO stat -c '%U:%G' -- "$path" 2>/dev/null || true)"
    cur_mode="$($USE_SUDO stat -c '%a' -- "$path" 2>/dev/null || true)"
    if [[ "$cur_owner" != "$_WANT_OWNER" ]]; then
        _issue "${label} owner drift: ${path} (want ${_WANT_OWNER}, got ${cur_owner})"
        if _should_act; then
            $USE_SUDO chown "$_WANT_OWNER" "$path" && _fix "chown ${_WANT_OWNER} ${path}"
        fi
    fi
    if [[ -z "$cur_mode" || "$(_norm_mode "$cur_mode")" != "$(_norm_mode "$_WANT_MODE")" ]]; then
        _issue "${label} mode drift: ${path} (want ${_WANT_MODE}, got ${cur_mode}) -- it may hold inline credentials"
        if _should_act; then
            $USE_SUDO chmod "$_WANT_MODE" "$path" && _fix "chmod ${_WANT_MODE} ${path}"
        fi
    fi
}

_reconcile_config_perms() {
    $USE_SUDO test -f "$CONF_FILE" || return 0
    _conf_want
    _perm_check_one "$CONF_FILE" "config file"

    # Backups of the config are full copies, credentials included. Builds before
    # _conf_backup made them with the original's (possibly world-readable) mode.
    # The codebase has only ever created .bak-expand-* and .bak-reconcile; the
    # wider .bak* / .orig / .backup patterns catch hand-made copies. The .example
    # file is not a backup and is never matched. Regular files only (no symlinks).
    local dir base f
    dir="$(dirname -- "$CONF_FILE")"
    base="$(basename -- "$CONF_FILE")"
    while IFS= read -r -d '' f; do
        _perm_check_one "$f" "config backup"
    done < <($USE_SUDO find "$dir" -maxdepth 1 -type f \( -name "${base}.bak*" -o -name "${base}.orig" -o -name "${base}.backup*" \) -print0 2>/dev/null)
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
                    # backup first, private (sed -i.SUFFIX would inherit the file's mode)
                    if _conf_backup ".bak-reconcile" && $USE_SUDO sed -i "/^${key}=/d" "$CONF_FILE"; then
                        _fix "stripped ${key} from ${CONF_FILE} (backup: ${_BAK})"
                    else
                        _issue "could not back up and strip ${key} from ${CONF_FILE}"
                    fi
                fi
                ;;
        esac
    done
}

# ---------------------------------------------------------------------------
# Step 6b — ${VAR} references in path-valued config keys
# systemd EnvironmentFile= does not expand them, so a service gets the literal
# text and resolves it as a relative path. The app expands references itself
# now; this step makes the installed file say what it means.
#
# A reference is resolved ONLY from (1) the file's own settings, then (2) a
# fixed map of what the service units themselves provide: AUDIOBOOKS_HOME and
# AUDIOBOOKS_VAR_DIR from the install layout this run was given (LIB_DIR,
# STATE_DIR), and HOME, which every unit sets to AUDIOBOOKS_VAR_DIR. The
# operator's own environment (HOME, PROJECT_DIR, ...) is NEVER consulted: it is
# not the service's, and a project-tree path must never land in an installed
# config. Anything else is reported and left untouched -- never guessed.
#
# Report mode lists every line. Enforce mode saves a timestamped backup beside
# the file (a sibling, not inside a scanned drop-in directory), then replaces
# the file atomically with an owner/mode-preserving copy. Only simply-quoted
# lines are rewritten; an unrecognised form is reported, not mangled.
# ---------------------------------------------------------------------------
declare -A _CFG_RAW=()
_CFG_STACK=()
_CFG_TMP=""
_CFG_TMP2=""
trap '[[ -n "${_CFG_TMP:-}" ]] && rm -f -- "${_CFG_TMP:?}"; [[ -n "${_CFG_TMP2:-}" ]] && $USE_SUDO rm -f -- "${_CFG_TMP2:?}"' EXIT

# Parse one config line into _P_LEAD _P_EXPORT _P_KEY _P_EQ _P_VAL _P_Q _P_TAIL.
# Returns 1 when the line is not a simple, recognisable KEY=VALUE assignment.
_cfg_parse_line() {
    local line="$1"
    local sq="'"
    local dq_re='^([[:space:]]*)(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)([[:space:]]*=[[:space:]]*)"([^"]*)"([[:space:]]+#.*)?$'
    local sq_re="^([[:space:]]*)(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)([[:space:]]*=[[:space:]]*)${sq}([^${sq}]*)${sq}([[:space:]]+#.*)?\$"
    local uq_re="^([[:space:]]*)(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)([[:space:]]*=[[:space:]]*)([^[:space:]#\"${sq}]*)([[:space:]]+#.*)?\$"
    if [[ "$line" =~ $dq_re ]]; then
        _P_Q='"'
    elif [[ "$line" =~ $sq_re ]]; then
        _P_Q="$sq"
    elif [[ "$line" =~ $uq_re ]]; then
        _P_Q=""
    else
        return 1
    fi
    _P_LEAD="${BASH_REMATCH[1]}"
    _P_EXPORT="${BASH_REMATCH[2]}"
    _P_KEY="${BASH_REMATCH[3]}"
    _P_EQ="${BASH_REMATCH[4]}"
    _P_VAL="${BASH_REMATCH[5]}"
    _P_TAIL="${BASH_REMATCH[6]}"
}

# Expand references in $2 (the value of key $1) into the global _CFG_OUT.
# Lookup is by NAME ONLY (no ${!ref}), so no shell variable of this script or of
# the operator's environment can leak in. Sets _CFG_ERR and returns 1 when a
# reference cannot be resolved from the allowed sources.
_cfg_expand() {
    local _x_key="$1" _x_value="$2" _x_s _x_ref _x_out="" _x_rest _x_raw
    # shellcheck disable=SC2016  # the regex is deliberately single-quoted
    local _x_re='^([^$]*)\$(\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))(.*)$'
    for _x_s in "${_CFG_STACK[@]}"; do
        if [[ "$_x_s" == "$_x_key" ]]; then
            _CFG_ERR="circular reference (${_CFG_STACK[*]} -> ${_x_key})"
            return 1
        fi
    done
    _CFG_STACK+=("$_x_key")
    _x_rest="$_x_value"
    while [[ "$_x_rest" =~ $_x_re ]]; do
        _x_ref="${BASH_REMATCH[3]:-${BASH_REMATCH[4]}}"
        _x_out+="${BASH_REMATCH[1]}"
        _x_rest="${BASH_REMATCH[5]}"
        if [[ -n "${_CFG_RAW[$_x_ref]+x}" ]]; then
            _x_raw="${_CFG_RAW[$_x_ref]}"
        elif [[ "$_x_ref" == "AUDIOBOOKS_HOME" && -n "${LIB_DIR:-}" ]]; then
            _x_raw="$LIB_DIR"
        elif [[ ("$_x_ref" == "AUDIOBOOKS_VAR_DIR" || "$_x_ref" == "HOME") && -n "${STATE_DIR:-}" ]]; then
            _x_raw="$STATE_DIR" # units set HOME=${AUDIOBOOKS_VAR_DIR}
        else
            _CFG_ERR="undefined variable ${_x_ref} (not set in this file or provided by the service units)"
            return 1
        fi
        _cfg_expand "$_x_ref" "$_x_raw" || return 1
        _x_out+="$_CFG_OUT"
    done
    unset "_CFG_STACK[$((${#_CFG_STACK[@]} - 1))]"
    _CFG_STACK=("${_CFG_STACK[@]}")
    _CFG_OUT="${_x_out}${_x_rest}"
}

_reconcile_config_references() {
    $USE_SUDO test -f "$CONF_FILE" || return 0
    [[ "${#CONFIG_PATH_KEYS[@]}" -gt 0 ]] || return 0

    local line key k is_path
    local -a hits=()
    local -A hit_form=()
    _CFG_RAW=()
    # Read through the same privilege path as every other /etc access here.
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        if _cfg_parse_line "$line"; then
            _CFG_RAW["$_P_KEY"]="$_P_VAL"
            key="$_P_KEY"
            hit_form["$key"]=ok
        elif [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*= ]]; then
            key="${BASH_REMATCH[2]}"
            hit_form["$key"]=odd
        else
            continue
        fi
        is_path=""
        for k in "${CONFIG_PATH_KEYS[@]}"; do [[ "$k" == "$key" ]] && is_path=1; done
        [[ -n "$is_path" && "$line" == *'$'* ]] && hits+=("$key")
    done < <($USE_SUDO cat -- "$CONF_FILE")
    [[ "${#hits[@]}" -gt 0 ]] || return 0

    local -A new_values=()
    for key in "${hits[@]}"; do
        if [[ "${hit_form[$key]}" == odd ]]; then
            _issue "unexpanded reference in ${CONF_FILE}: ${key} uses a quoting form the reconciler will not rewrite; edit it by hand (the app still expands it at runtime)"
            continue
        fi
        _CFG_STACK=()
        if _cfg_expand "$key" "${_CFG_RAW[$key]}"; then
            # shellcheck disable=SC1003  # matching a literal backslash, not escaping a quote
            if [[ "$_CFG_OUT" == *'"'* || "$_CFG_OUT" == *'\'* || "$_CFG_OUT" == *'$'* || "$_CFG_OUT" == *'`'* ]]; then
                _issue "unexpanded reference in ${CONF_FILE}: ${key}=${_CFG_RAW[$key]} would expand to a value with quote/backslash/\$ characters (${_CFG_OUT}); left as is"
            else
                new_values["$key"]="$_CFG_OUT"
                _issue "unexpanded reference in ${CONF_FILE}: ${key}=${_CFG_RAW[$key]} (systemd will not expand it) -> ${_CFG_OUT}"
            fi
        else
            _issue "unexpanded reference in ${CONF_FILE}: ${key}=${_CFG_RAW[$key]} cannot be expanded (${_CFG_ERR}); left as is"
        fi
    done
    _should_act || return 0
    [[ "${#new_values[@]}" -gt 0 ]] || return 0

    local backup cr
    if ! _conf_backup ".bak-expand-$(date +%Y%m%d-%H%M%S)"; then
        _issue "could not back up ${CONF_FILE}; references not rewritten"
        return 0
    fi
    backup="$_BAK"
    # Private scratch copy (0600, removed by the EXIT trap), then an atomic swap:
    # the replacement is created beside the target, given the target's owner and
    # mode, and moved over it, so a reader never sees a half-written config.
    local old_umask
    old_umask="$(umask)"
    umask 077
    _CFG_TMP="$(mktemp)"
    umask "$old_umask"
    while IFS= read -r line || [[ -n "$line" ]]; do
        cr=""
        if [[ "$line" == *$'\r' ]]; then
            cr=$'\r'
            line="${line%$'\r'}"
        fi
        if _cfg_parse_line "$line" && [[ -n "${new_values[$_P_KEY]+x}" ]]; then
            printf '%s%s%s%s"%s"%s%s\n' "$_P_LEAD" "$_P_EXPORT" "$_P_KEY" "$_P_EQ" \
                "${new_values[$_P_KEY]}" "$_P_TAIL" "$cr" >>"$_CFG_TMP"
        else
            printf '%s%s\n' "$line" "$cr" >>"$_CFG_TMP"
        fi
    done < <($USE_SUDO cat -- "$CONF_FILE")
    _CFG_TMP2="$($USE_SUDO mktemp "${CONF_FILE}.XXXXXX")" || {
        _issue "could not create a replacement beside ${CONF_FILE}; backup left at ${backup}"
        return 0
    }
    if $USE_SUDO tee "$_CFG_TMP2" <"$_CFG_TMP" >/dev/null &&
        $USE_SUDO chown --reference="$CONF_FILE" "$_CFG_TMP2" &&
        $USE_SUDO chmod --reference="$CONF_FILE" "$_CFG_TMP2" &&
        $USE_SUDO mv -f -- "$_CFG_TMP2" "$CONF_FILE"; then
        _CFG_TMP2=""
        for key in "${!new_values[@]}"; do
            _fix "expanded ${key} in ${CONF_FILE} (backup: ${backup})"
        done
    else
        _issue "could not replace ${CONF_FILE}; original untouched, backup at ${backup}"
    fi
}

# ---------------------------------------------------------------------------
# Step 6c — state-dir / data-location mismatch (REPORT ONLY, always)
# The database and covers default to <AUDIOBOOKS_VAR_DIR>/db/audiobooks.db and
# <AUDIOBOOKS_VAR_DIR>/covers in BOTH config.py and the shell loader. On an
# install where an operator moved VAR_DIR but the data still sits under the
# built-in default, consumers now look at an empty place (and sqlite creates an
# empty DB there on first connect). This only warns -- it never moves data and
# never creates anything -- whatever RECONCILE_MODE says. Placed here rather
# than in an upgrade.sh preflight because the reconciler already holds the
# resolved layout (STATE_DIR) and the conf-file reader, and install.sh runs it
# too. An explicit AUDIOBOOKS_DATABASE / AUDIOBOOKS_COVERS in the config
# silences the matching check: the operator has said where the data is.
# ---------------------------------------------------------------------------
_cfg_has_data() {
    if $USE_SUDO test -d "$1"; then
        [[ -n "$($USE_SUDO ls -A -- "$1" 2>/dev/null)" ]]
    else
        $USE_SUDO test -e "$1"
    fi
}

_reconcile_state_dir_mismatch() {
    # The built-in default is read from its canonical home (the shell loader), not
    # repeated here. DEFAULT_VAR_DIR is an override for the regression suite only.
    local default="${DEFAULT_VAR_DIR:-}" state="${STATE_DIR:-}"
    if [[ -z "$default" && -f "${PROJECT_DIR:-}/lib/audiobook-config.sh" ]]; then
        default="$(grep -oP '^: "\$\{AUDIOBOOKS_VAR_DIR:=\K[^}]+' "${PROJECT_DIR}/lib/audiobook-config.sh" | head -1)"
    fi
    [[ -n "$default" && -n "$state" && "${state%/}" != "${default%/}" ]] || return 0

    local conf_text=""
    if $USE_SUDO test -f "$CONF_FILE"; then
        conf_text="$($USE_SUDO cat -- "$CONF_FILE" 2>/dev/null || true)"
    fi
    local pair key rel
    for pair in "AUDIOBOOKS_DATABASE|db/audiobooks.db" "AUDIOBOOKS_COVERS|covers"; do
        IFS='|' read -r key rel <<<"$pair"
        if grep -qE "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" <<<"$conf_text"; then
            continue
        fi
        # "Has data" means: the file exists, or the directory holds something. An
        # EMPTY directory at the new location does not count -- enforce mode's
        # REQUIRED_DIRS step creates exactly that, which must not hide the mismatch.
        if _cfg_has_data "${default}/${rel}" && ! _cfg_has_data "${state}/${rel}"; then
            _issue "DATA LOCATION MISMATCH: ${key} defaults to ${state}/${rel} (AUDIOBOOKS_VAR_DIR is not the built-in default) but the data is at ${default}/${rel} and ${state}/${rel} does not exist or is empty. Services will see no data (a database would be created empty). Fix: move the data to ${state}/${rel}, or set ${key} explicitly to ${default}/${rel}. Nothing was moved or created."
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 7 — audit optional credential files (non-fatal — files may not exist)
# Format: <path>|<owner>:<group>|<mode>|<env-var-name>
# If the file exists, verify owner/group and mode match; log warning on drift.
# If the file does not exist, no warning — optional by design.
# ---------------------------------------------------------------------------
# Normalise a mode string so the manifest's spelling and stat's agree.
# `stat -c %a` prints 600; the manifest declares 0600. Those are the SAME mode,
# but a string compare never matches — so the drift could never clear and chmod
# re-ran on every single pass, printing four identical warnings per deploy
# (Audiobook-Manager-nsz). A check that always fires trains the operator to skim
# the drift section, which is exactly where a real permission regression would
# appear. Round-tripping through octal converges both spellings and preserves
# setgid/sticky bits (2770 -> 2770).
_norm_mode() { printf '%04o' "$((8#${1}))"; }

_reconcile_optional_credentials() {
    [[ "${#OPTIONAL_CREDENTIAL_FILES[@]}" -eq 0 ]] && return 0
    local entry path owner mode _envvar current_owner current_mode
    for entry in "${OPTIONAL_CREDENTIAL_FILES[@]}"; do
        IFS='|' read -r path owner mode _envvar <<<"$entry"
        [[ -f "$path" ]] || continue # optional — absence is not a drift

        current_owner=$(stat -c '%U:%G' "$path" 2>/dev/null || true)
        current_mode=$(stat -c '%a' "$path" 2>/dev/null || true)

        if [[ "$current_owner" != "$owner" ]]; then
            _issue "credential file owner drift: $path (want $owner, got $current_owner)"
            if _should_act; then
                $USE_SUDO chown "$owner" "$path" 2>/dev/null && _fix "chown $owner $path"
            fi
        fi
        if [[ "$(_norm_mode "$current_mode")" != "$(_norm_mode "$mode")" ]]; then
            _issue "credential file mode drift: $path (want $mode, got $current_mode)"
            if _should_act; then
                $USE_SUDO chmod "$mode" "$path" 2>/dev/null && _fix "chmod $mode $path"
            fi
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 9 — clean __pycache__ directories under LIB_DIR (stale bytecode
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
# Step 10 — report
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
    _reconcile_obsolete_root_paths
    _reconcile_dirs
    _reconcile_venvs
    _reconcile_units
    _reconcile_wrappers
    _reconcile_config_perms
    _reconcile_config
    _reconcile_config_references
    _reconcile_state_dir_mismatch
    _reconcile_optional_credentials
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
