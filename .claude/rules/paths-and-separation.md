# No Hardcoded Paths & Project/App Separation

## No Hardcoded Paths — EVER

- **NEVER** write literal paths (`/run/audiobooks`, `/var/lib/audiobooks`, `/srv/audiobooks`) in scripts, services or code
- **ALWAYS** use variables (`$AUDIOBOOKS_RUN_DIR`, `$AUDIOBOOKS_VAR_DIR`, `$AUDIOBOOKS_DATA`, …); if one is missing, **ADD IT** to `lib/audiobook-config.sh` first
- Why: users set paths in `/etc/audiobooks/audiobooks.conf`; literals break customization and fail silently
- A pre-commit hook blocks hardcoded paths: replace with the variable (adding it if needed), re-commit

| Variable (lib/audiobook-config.sh) | Default | Purpose |
|----------|---------|---------|
| `AUDIOBOOKS_DATA` | `/srv/audiobooks` | Main data directory |
| `AUDIOBOOKS_LIBRARY` | `${AUDIOBOOKS_DATA}/Library` | Converted audiobooks |
| `AUDIOBOOKS_SOURCES` | `${AUDIOBOOKS_DATA}/Sources` | Source files |
| `AUDIOBOOKS_RUN_DIR` | `/var/lib/audiobooks/.run` | Runtime (locks, FIFOs) |
| `AUDIOBOOKS_VAR_DIR` | `/var/lib/audiobooks` | Persistent state |
| `AUDIOBOOKS_STAGING` | `/tmp/audiobook-staging` | Conversion staging |
| `AUDIOBOOKS_DATABASE` | varies | SQLite database path |

## Complete Separation of Project and Application

**Project and installed application are COMPLETELY SEPARATE with NO DEPENDENCIES.**

**Project (dev):** `<your-projects-dir>/Audiobook-Manager/` (git repo); `./library/testdata/` (synthetic test data, NOT production); `./library/backend/audiobooks-dev.db` (dev DB, 64KB, 5 test records); `./config.env` (dev paths within project, ports 9090/6001 vs production 8443/5001).

**Installed (prod):** `/opt/audiobooks/` (app code); `/opt/audiobooks/scripts/` (symlinked from `/usr/local/bin/`); `${AUDIOBOOKS_DATA}` (default `/srv/audiobooks/`; Library, Sources, logs); `/usr/local/lib/audiobooks/` (shared config library); `/etc/audiobooks/` (system config); `/etc/systemd/system/audiobook*.service`.

### NO CROSS-REFERENCES ALLOWED

- Project code must NEVER reference `${AUDIOBOOKS_DATA}` or `/opt/audiobooks/`
- Application must NEVER reference the project working tree
- Symlinks point to APPLICATION, not PROJECT (`/usr/local/bin/` -> `/opt/audiobooks/scripts/`)

### Deployment Workflow

```bash
./upgrade.sh --from-project . --target /opt/audiobooks --yes       # standard production deploy
./upgrade.sh --from-project . --target /opt/audiobooks --dry-run   # dry run
./upgrade.sh --from-project . --remote <vm-host> --yes             # remote VM: stop, backup, sync, venv, restart
./upgrade.sh --check --target /opt/audiobooks                      # check for updates
./upgrade.sh --backup --target /opt/audiobooks                     # upgrade with backup
```
