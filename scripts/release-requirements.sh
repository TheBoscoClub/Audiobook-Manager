#!/bin/bash
# release-requirements.sh — Release functional-requirements manifest
#
# Declares what MUST exist on disk, in config, and in the database for this
# release to be functional end-to-end. upgrade.sh and install.sh source this
# file and call validate_release_requirements() after their work completes.
# Missing items produce actionable errors with remediation snippets.
#
# Why this exists:
#   v8.3.7.1 shipped a streaming pipeline whose DB columns were not applied
#   by the data-migration dispatcher (pre-existing version-ordering bug in
#   upgrade.sh), whose RunPod endpoint keys were never installed by
#   install.sh, and whose systemd unit was wiped by a separate audit-cleanup
#   regression. No component of the install/upgrade pipeline noticed any of
#   this because there was no "after the file copy, does the thing actually
#   work?" check. This file is that check — declarative, reviewable, and
#   evolvable per-release.
#
# Contract:
#   - Each RELEASE updates this file when it ADDS a requirement. Never remove
#     an entry on upgrade — a rollback might need the older contract.
#   - Validation is NON-FATAL by default (warnings + actionable snippets).
#     The POST-UPGRADE SMOKE TEST (smoke_probe.sh, sibling file) is the hard
#     gate — it actually tries to exercise the feature and fails upgrade if
#     the feature breaks.
#
# Consumed by:
#   - upgrade.sh :: validate_release_requirements() call near end of do_upgrade
#   - install.sh :: validate_release_requirements() call after systemd enable
#   - scripts/smoke_probe.sh :: uses REQUIRED_DB_COLUMNS for schema probes

# shellcheck disable=SC2034  # arrays consumed by validator, not this file

RELEASE_REQUIREMENTS_VERSION="8.3.9"

# ─── Required config keys ────────────────────────────────────────────────────
# Format: KEY|SEVERITY|FEATURE|PURPOSE
#   SEVERITY: required | required_for_feature | optional
#   FEATURE:  short name of the feature that depends on this key
#   PURPOSE:  one-line description for the error message
#
# Missing "required" → upgrade fails.
# Missing "required_for_feature" → warning with feature-disabled banner.
# Missing "optional" → info log only.
#
# No literal secrets or endpoint IDs here — those are per-deployment.
#
# STT backend keys are deliberately NOT listed as project-level requirements.
# The project supports multiple interchangeable STT backends — RunPod serverless,
# Vast.ai serverless, local whisper-gpu service (CUDA/ROCm), or CPU-only
# faster-whisper — and the choice is an operator deployment decision, not a
# project contract. Functional readiness of whichever backend(s) the operator
# configured is validated by smoke_probe.sh (_probe_stt_providers), not
# declaratively here. DeepL is currently the only supported translation
# backend, so its key remains a feature-gated project requirement.
REQUIRED_CONFIG_KEYS=(
    "AUDIOBOOKS_DEEPL_API_KEY|required_for_feature|translation|DeepL API key for text translation (get one at https://www.deepl.com/pro-api)"
    "AUDIOBOOKS_TTS_PROVIDER|optional|streaming|TTS engine selector (edge-tts / xtts / etc). Default: edge-tts"
)

# ─── Required systemd units ──────────────────────────────────────────────────
# Every unit in this list MUST exist under /etc/systemd/system/ and be
# enabled. Missing OR disabled → upgrade fails (hard gate).
REQUIRED_SYSTEMD_UNITS=(
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
)

# ─── Required DB schema ──────────────────────────────────────────────────────
# Format: TABLE.COLUMN — must exist as a column on that table.
# Missing → upgrade fails (hard gate). The data-migration dispatcher is
# responsible for adding these; this list is the invariant it must produce.
REQUIRED_DB_COLUMNS=(
    "audiobooks.chapter_count"
    "streaming_segments.retry_count"
    "streaming_segments.source_vtt_content"
    "streaming_segments.origin"
)

# ─── Required DB tables ──────────────────────────────────────────────────────
# Features that hinge on a whole new table (not just a column add). Missing →
# upgrade fails. The data-migration dispatcher is responsible for creating
# these; this list is the invariant it must produce.
#
# v8.3.8 shipped sampler_jobs via data-migration 008, then the GitHub release
# tarball omitted data-migrations/ entirely — so every fresh install/upgrade
# from the tarball was missing this table. This gate catches exactly that.
REQUIRED_DB_TABLES=(
    "sampler_jobs"
    "translation_monitor_events"
)

# ─── Validator ───────────────────────────────────────────────────────────────
# Invoked by upgrade.sh and install.sh after all file/schema work completes.
# Returns 0 on clean, 1 on any hard failure. Prints remediation snippets.

validate_release_requirements() {
    local conf_file="${1:-/etc/audiobooks/audiobooks.conf}"
    local db_path="${2:-}"
    local use_sudo="${3:-}"
    local systemd_dir="${4:-/etc/systemd/system}"

    local hard_fail=0
    local soft_warn=0
    local missing_keys=()
    local missing_units=()
    local missing_columns=()
    local disabled_by_missing_key=()

    # Color codes (fall back to empty if upgrade.sh's BLUE/RED/etc are not set).
    local _red="${RED:-\033[0;31m}"
    local _yellow="${YELLOW:-\033[1;33m}"
    local _green="${GREEN:-\033[0;32m}"
    local _blue="${BLUE:-\033[0;34m}"
    local _nc="${NC:-\033[0m}"

    echo -e "${_blue}=== Validating release requirements (v${RELEASE_REQUIREMENTS_VERSION}) ===${_nc}"

    # ─── Config keys ───
    if [[ -f "$conf_file" ]]; then
        for entry in "${REQUIRED_CONFIG_KEYS[@]}"; do
            IFS='|' read -r key severity feature purpose <<<"$entry"
            local value=""
            value=$(grep -oP "^${key}=\K.*" "$conf_file" 2>/dev/null | head -1)
            value="${value%\"}"
            value="${value#\"}"
            if [[ -z "$value" ]]; then
                case "$severity" in
                    required)
                        missing_keys+=("$key|required|$purpose")
                        hard_fail=1
                        ;;
                    required_for_feature)
                        disabled_by_missing_key+=("$feature:$key|$purpose")
                        soft_warn=1
                        ;;
                    *)
                        ;; # optional — info only
                esac
            fi
        done
    else
        echo -e "  ${_red}ERROR: config file not found: $conf_file${_nc}"
        hard_fail=1
    fi

    # ─── systemd units ───
    for unit in "${REQUIRED_SYSTEMD_UNITS[@]}"; do
        if [[ ! -f "${systemd_dir}/${unit}" ]]; then
            missing_units+=("$unit")
            hard_fail=1
        fi
    done

    # ─── DB columns ───
    local missing_tables=()
    if [[ -n "$db_path" ]] && [[ -f "$db_path" ]]; then
        local _sqlite_cmd="sqlite3"
        if [[ -n "$use_sudo" ]]; then
            _sqlite_cmd="sudo -u audiobooks sqlite3"
        fi
        for entry in "${REQUIRED_DB_COLUMNS[@]}"; do
            local table="${entry%%.*}"
            local column="${entry##*.}"
            # Verify column exists via PRAGMA.
            if ! $_sqlite_cmd "$db_path" "PRAGMA table_info(${table});" 2>/dev/null \
                | awk -F'|' '{print $2}' | grep -qx "$column"; then
                missing_columns+=("$entry")
                hard_fail=1
            fi
        done
        # ─── DB tables (whole-table features) ───
        for tbl in "${REQUIRED_DB_TABLES[@]}"; do
            if ! $_sqlite_cmd "$db_path" \
                "SELECT name FROM sqlite_master WHERE type='table' AND name='${tbl}';" \
                2>/dev/null | grep -qx "$tbl"; then
                missing_tables+=("$tbl")
                hard_fail=1
            fi
        done
    fi

    # ─── Report ───
    if [[ ${#missing_keys[@]} -gt 0 ]]; then
        echo -e "  ${_red}✗ Required config keys missing:${_nc}"
        for entry in "${missing_keys[@]}"; do
            IFS='|' read -r key sev purpose <<<"$entry"
            echo -e "    ${_red}${key}${_nc} — $purpose"
            echo "      Add to $conf_file:"
            echo "      ${key}=\"<value>\""
        done
    fi

    if [[ ${#disabled_by_missing_key[@]} -gt 0 ]]; then
        echo -e "  ${_yellow}⚠ Optional features disabled by missing config:${_nc}"
        for entry in "${disabled_by_missing_key[@]}"; do
            IFS='|' read -r feature_key purpose <<<"$entry"
            local feature="${feature_key%%:*}"
            local key="${feature_key##*:}"
            echo -e "    ${_yellow}${feature}${_nc}: missing ${key}"
            echo "      $purpose"
        done
    fi

    if [[ ${#missing_units[@]} -gt 0 ]]; then
        echo -e "  ${_red}✗ Required systemd units missing:${_nc}"
        for unit in "${missing_units[@]}"; do
            echo -e "    ${_red}${systemd_dir}/${unit}${_nc}"
        done
        echo -e "  ${_yellow}  Run: sudo cp ${PROJECT_DIR:-\$PROJECT}/systemd/audiobook*.{service,target,path,timer} ${systemd_dir}/ && sudo systemctl daemon-reload${_nc}"
    fi

    if [[ ${#missing_columns[@]} -gt 0 ]]; then
        echo -e "  ${_red}✗ Required database columns missing:${_nc}"
        for col in "${missing_columns[@]}"; do
            echo -e "    ${_red}${col}${_nc}"
        done
        echo -e "  ${_yellow}  Re-run the data-migration dispatcher:${_nc}"
        echo "    ./upgrade.sh --from-project . --target ${APP_DIR:-/opt/audiobooks} --yes --force"
    fi

    if [[ ${#missing_tables[@]} -gt 0 ]]; then
        echo -e "  ${_red}✗ Required database tables missing:${_nc}"
        for tbl in "${missing_tables[@]}"; do
            echo -e "    ${_red}${tbl}${_nc}"
        done
        echo -e "  ${_yellow}  Re-run the data-migration dispatcher:${_nc}"
        echo "    ./upgrade.sh --from-project . --target ${APP_DIR:-/opt/audiobooks} --yes --force"
    fi

    if [[ $hard_fail -eq 0 ]] && [[ $soft_warn -eq 0 ]]; then
        echo -e "  ${_green}✓ All release requirements satisfied${_nc}"
    elif [[ $hard_fail -eq 0 ]]; then
        echo -e "  ${_yellow}⚠ Release functional; some optional features disabled${_nc}"
    fi

    return $hard_fail
}
