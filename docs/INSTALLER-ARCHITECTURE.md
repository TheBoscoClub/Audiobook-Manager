# Installer Architecture

Source of truth for how `install.sh`, `uninstall.sh`, `upgrade.sh`, and their
supporting scripts work together. Read this before touching any of them.

## Who Owns What

| Concern | Canonical source | Never duplicate |
|---|---|---|
| Default path values | `library/config.py` (Python) and `lib/audiobook-config.sh` (bash) — both MUST agree | Any other script, systemd unit, or docs file |
| Install layout (dirs, units, wrappers, phantoms) | `scripts/install-manifest.sh` | Inlined lists in install.sh / uninstall.sh |
| Drift remediation | `scripts/reconcile-filesystem.sh` + `config-migrations/*.sh` | Ad-hoc `sed` calls in upgrade.sh |
| State preservation across wipe | `uninstall.sh::stage_preserved_state` + `restore_preserved_state` | Copy-pasted `cp -a` blocks in install.sh |

When you change a default, change **both** `library/config.py` and
`lib/audiobook-config.sh` in the same commit. `test_install_manifest_reconciler.py::test_config_canonical_defaults_are_covered_in_config_py`
asserts the pair stays consistent.

## Drift: The Bug Class

**Drift** = a hardcoded path or config value in one place stops matching the
canonical default somewhere else. It silently forks the system into a
split-brain where bash-sourced scripts and the Python app compute different
paths for the same key.

### The 2026-04 incident

`install.sh` historically wrote these lines into `audiobooks.conf`:

```bash
AUDIOBOOKS_COVERS="${AUDIOBOOKS_HOME}/library/web-v2/covers"
AUDIOBOOKS_DATABASE="/var/lib/audiobooks/audiobooks.db"
```

Then `library/config.py` was updated to:

```python
AUDIOBOOKS_COVERS = _var_dir / "covers"                      # /var/lib/audiobooks/covers
AUDIOBOOKS_DATABASE = _var_dir / "db" / "audiobooks.db"      # /var/lib/audiobooks/db/audiobooks.db
```

The bash default in `lib/audiobook-config.sh` was updated to match Python,
but the `install.sh` template and `audiobooks.conf.example` were not. Every
install after that date shipped a config file whose stale lines **overrode**
the correct defaults. Result: cover art 404s, split DB paths, wasted debugging.

### Prevention

1. **No hardcoded defaults in `audiobooks.conf.example`** — every path key is
   commented out with only the default documented. The pre-commit hook
   (`.git/hooks/pre-commit`) blocks new commits that add hardcoded paths.
2. **`install.sh` never writes path overrides into `audiobooks.conf`** —
   it only writes user-supplied non-default values.
3. **Manifest + reconciler** — `scripts/install-manifest.sh` declares every
   canonical path, unit, and wrapper. `scripts/reconcile-filesystem.sh`
   enforces it (creates missing, deletes phantoms, strips legacy config lines).
4. **Config migrations** — one-shot `config-migrations/NNN_*.sh` scripts run
   by `upgrade.sh::apply_config_migrations` repair already-deployed configs.

## Subset-Preservation: The Other Bug Class

`--keep-data` used to preserve only `/srv/audiobooks/{Library,Sources,Supplements}`
and silently wiped `/var/lib/audiobooks` (DB, `auth.db`, covers cache) plus
`/etc/audiobooks/audiobooks.conf` and `auth.key`. The name promised one thing,
the implementation delivered another.

### The fix

`uninstall.sh::stage_preserved_state` now stages **all** user state before
`remove_config_and_state` runs, into a `mktemp -d` staging directory with an
`EXIT` trap for cleanup:

| Item | Source (system) | Staged as |
|---|---|---|
| Main DB dir | `/var/lib/audiobooks/db/` | `db/` |
| Auth DB | `/var/lib/audiobooks/auth.db` | `auth.db` |
| Auth signing key | `/etc/audiobooks/auth.key` | `auth.key` |
| Covers cache | `/var/lib/audiobooks/covers/` | `covers/` |
| User config | `/etc/audiobooks/audiobooks.conf` | `audiobooks.conf` |

`restore_preserved_state` replays the staging dir after the wipe, re-applies
`chmod 0600` to `auth.key`, and `chown`s everything back to the service
account. `--delete-data` short-circuits staging entirely (preserves nothing).

This is covered by `library/tests/test_uninstall_keep_data.py`, which runs
`uninstall.sh --user` end-to-end against a scratch `$HOME`.

### Interaction with `install.sh --fresh-install`

Because `uninstall.sh` now restores `audiobooks.conf`, a fresh install that
calls uninstall first would end up with the **old** config and never pick up
new default keys introduced in the new version. `do_fresh_install` therefore
has a **Step 3b**: after uninstall returns, delete the restored
`audiobooks.conf` so `install.sh` writes a fresh default, then Step 5 merges
the user's non-default overrides (from `fresh_backup_dir`) on top.

The `auth.db` staging path inside `do_fresh_install` was also fixed to use
the canonical `${state_src}/auth.db` with a fallback to the legacy
`${state_src}/db/auth.db` location for any pre-v8 installs that still have
it under `db/`.

## File-by-file Map

| File | Role |
|---|---|
| `install.sh` | One-shot fresh install + `--fresh-install` reinstall. Owns `do_fresh_install`, `do_install_system`, `do_install_user`. |
| `uninstall.sh` | Dynamic-discovery teardown. Owns preservation helpers, `remove_*` functions, `do_{system,user}_uninstall`. |
| `upgrade.sh` | Version-to-version upgrade. Runs config migrations, calls reconciler, handles remote deploys. |
| `lib/audiobook-config.sh` | Canonical bash defaults. Sourced by every script that needs paths. |
| `library/config.py` | Canonical Python defaults. MUST agree with `lib/audiobook-config.sh`. |
| `scripts/install-manifest.sh` | Declarative arrays: `REQUIRED_VENVS`, `PHANTOM_PATHS`, `REQUIRED_DIRS`, `CANONICAL_UNITS`, `CANONICAL_WRAPPERS`, `CONFIG_CANONICAL_DEFAULTS`. Pure data — no side effects. |
| `scripts/reconcile-filesystem.sh` | Reads the manifest, reports or enforces. Two modes: `--report` (read-only audit) and `--enforce` (mutating). |
| `config-migrations/001_add_run_dir.sh` | One-shot: ensure `AUDIOBOOKS_RUN_DIR` present in config. |
| `config-migrations/002_strip_legacy_path_overrides.sh` | One-shot: strip legacy `AUDIOBOOKS_COVERS` and `AUDIOBOOKS_DATABASE` overrides; preserves user customization. |
| `etc/audiobooks.conf.example` | Template config. **All path keys commented out** to prevent future drift. |

## What Got Deleted

The v8.1.0.1 cleanup removed three legacy installer fragments:

- `install-system.sh`
- `install-user.sh`
- `install-services.sh`

These were per-layer installers that duplicated logic already in `install.sh`.
Every call site now goes through `install.sh --system` or `install.sh --user`.
Do not resurrect them — add to `install.sh` or the manifest instead.

## Rules For Future Installer Changes

1. **Never hardcode a path literal.** Use variables from
   `lib/audiobook-config.sh`. The pre-commit hook enforces this.
2. **When a default changes, change it in exactly three places in one commit:**
   `library/config.py`, `lib/audiobook-config.sh`, and (if the old value was
   ever written to disk) a new `config-migrations/NNN_*.sh` migration.
3. **Never duplicate the manifest.** If install.sh needs a list of units,
   source `scripts/install-manifest.sh`. Inline arrays are forbidden.
4. **Preservation is an invariant, not a feature.** Any new piece of user
   state (a new DB, a new key file) added to the app must be added to
   `stage_preserved_state` / `restore_preserved_state` in the same PR.
5. **`audiobooks.conf.example` documents defaults only.** Never uncomment
   a path key in the example template. If you need to help users find the
   default, put it in a comment above the line.
6. **Tests live in `library/tests/test_install_manifest_reconciler.py` and
   `library/tests/test_uninstall_keep_data.py`.** Add a test for every new
   invariant. The manifest/reconciler tests run without sudo or a VM; the
   uninstall tests use `--user` mode against a scratch `$HOME`.

## Running the Reconciler

```bash
# Audit only (no changes)
sudo bash scripts/reconcile-filesystem.sh --report

# Fix drift
sudo bash scripts/reconcile-filesystem.sh --enforce

# For a user-mode install (no sudo)
LIB_DIR="$HOME/.local/lib/audiobooks" \
STATE_DIR="$HOME/.local/var/lib/audiobooks" \
LOG_DIR="$HOME/.local/var/log/audiobooks" \
CONFIG_DIR="$HOME/.config/audiobooks" \
    bash scripts/reconcile-filesystem.sh --report
```

`upgrade.sh` calls the reconciler automatically in `--enforce` mode after
applying config migrations.
