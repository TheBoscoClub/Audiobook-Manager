# No Hardcoded Paths & Project/App Separation

## No Hardcoded Paths — EVER

**All paths in scripts, services, and code MUST use configuration variables.**

- **NEVER** write literal paths like `/run/audiobooks`, `/var/lib/audiobooks`, `/srv/audiobooks`
- **ALWAYS** use variables: `$AUDIOBOOKS_RUN_DIR`, `$AUDIOBOOKS_VAR_DIR`, `$AUDIOBOOKS_DATA`, etc.
- If a needed path variable doesn't exist, **ADD IT** to `lib/audiobook-config.sh` first

**Why**: End users configure their own paths in `/etc/audiobooks/audiobooks.conf`. Hardcoded paths break user customization and cause silent failures.

### Available Variables (from lib/audiobook-config.sh)

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUDIOBOOKS_DATA` | `/srv/audiobooks` | Main data directory |
| `AUDIOBOOKS_LIBRARY` | `${AUDIOBOOKS_DATA}/Library` | Converted audiobooks |
| `AUDIOBOOKS_SOURCES` | `${AUDIOBOOKS_DATA}/Sources` | Source files |
| `AUDIOBOOKS_RUN_DIR` | `/var/lib/audiobooks/.run` | Runtime (locks, FIFOs) |
| `AUDIOBOOKS_VAR_DIR` | `/var/lib/audiobooks` | Persistent state |
| `AUDIOBOOKS_STAGING` | `/tmp/audiobook-staging` | Conversion staging |
| `AUDIOBOOKS_DATABASE` | varies | SQLite database path |

### Pre-commit Hook

A pre-commit hook blocks commits containing hardcoded paths. If rejected:

1. Replace the literal path with the appropriate variable
2. If no variable exists, add one to `lib/audiobook-config.sh`
3. Re-run your commit

## Complete Separation of Project and Application

**This project and the installed application are COMPLETELY SEPARATE with NO DEPENDENCIES between them.**

### Project (Development)

| Location | Purpose |
|----------|---------|
| `<your-projects-dir>/Audiobook-Manager/` | Git repository, source code, development |
| `./library/testdata/` | Synthetic test data (NOT production) |
| `./library/backend/audiobooks-dev.db` | Development database (64KB, 5 test records) |
| `./config.env` | Development configuration (ports 9090/6001) |

### Installed Application (Production)

| Location | Purpose |
|----------|---------|
| `/opt/audiobooks/` | System application code |
| `/opt/audiobooks/scripts/` | Installed scripts (symlinked from `/usr/local/bin/`) |
| `${AUDIOBOOKS_DATA}` (default `/srv/audiobooks/`) | Production data (Library, Sources, logs) |
| `/usr/local/lib/audiobooks/` | Shared configuration library |
| `/etc/audiobooks/` | System configuration |
| `/etc/systemd/system/audiobook*.service` | Systemd services |

### NO CROSS-REFERENCES ALLOWED

- Project code must NEVER reference `${AUDIOBOOKS_DATA}` or `/opt/audiobooks/`
- Application must NEVER reference the project working tree
- Symlinks must point to APPLICATION, not PROJECT
- System scripts in `/usr/local/bin/` -> `/opt/audiobooks/scripts/`

### Development Mode

- `config.env` - Development paths within project
- `audiobooks-dev.db` - Small test database with synthetic data
- Ports 9090/6001 (different from production 8443/5001)

### Deployment Workflow

```bash
# Deploy to system installation (/opt/audiobooks) — standard production deploy
./upgrade.sh --from-project . --target /opt/audiobooks --yes

# Dry run to see what would happen
./upgrade.sh --from-project . --target /opt/audiobooks --dry-run

# Deploy to remote VM (full lifecycle: stop, backup, sync, venv, restart)
./upgrade.sh --from-project . --remote <vm-host> --yes

# Check for available updates
./upgrade.sh --check --target /opt/audiobooks

# Upgrade with backup
./upgrade.sh --backup --target /opt/audiobooks
```
