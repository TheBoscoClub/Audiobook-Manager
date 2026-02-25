# Testing — VM Isolation & Verification

## ALL Application Testing on test-audiobook-cachyos

**Dev machine is for unit tests and code editing ONLY. All integration, API, UI, and E2E tests MUST run against the dedicated test VM.**

- **Dev machine**: Unit tests, linting, static analysis, code editing
- **VM (test-audiobook-cachyos)**: Integration tests, API tests, UI/Playwright tests, auth tests, E2E tests
- **Before testing on pristine VM**: Run `./install.sh --system` first (creates audiobooks user/group, dirs, venv, DB, services), then deploy
- **Before testing on installed VM**: Deploy latest code with `./upgrade.sh --from-project . --remote 192.168.122.104 --yes`
- **`/test` handles this automatically**: Phase VM-lifecycle detects pristine state and auto-installs before tests run

### VM Connection Details

| Property | Value |
|----------|-------|
| Hostname | `test-audiobook-cachyos` / `192.168.122.104` |
| SSH user | `claude` |
| SSH key | `~/.ssh/id_ed25519` |
| API port | `5001` (HTTP) |
| Web port | `8443` (HTTPS, self-signed) |
| App path | `/opt/audiobooks` |
| Data path | `/srv/audiobooks` |
| SPICE display | `spice://127.0.0.1:5900` |

### VM Snapshots

| Snapshot | Description | Revert To |
|----------|-------------|-----------|
| `pristine-os-deps-2026-02-22` | **Authoritative** pristine CachyOS (kernel 6.19.3-2), all app deps (Python 3.14.3, ffmpeg 8.0.1, sqlite3 3.51.2, openssl 3.6.1, sqlcipher), tmpfs /tmp=4G, NO audiobook-manager installed | Fresh install testing, pre-test-run reset |

**This is the authoritative snapshot.** Before any test run that installs audiobook-manager, revert to this snapshot. After testing, revert again to restore pristine state.

**Revert procedure** (external snapshots — DISCARD changes, restore pristine):
```bash
sudo virsh destroy test-audiobook-cachyos   # stop VM if running
# Delete snapshot metadata
sudo virsh snapshot-delete test-audiobook-cachyos pristine-os-deps-2026-02-22 --metadata
# IMPORTANT: Do NOT commit the overlay — that bakes changes into the base!
# Just repoint VM directly to the base image (discarding overlay changes)
sudo virt-xml test-audiobook-cachyos --edit target=vda --disk path=/hddRaid1/VirtualMachines/test-audiobook-cachyos.qcow2
# Remove overlay file (discards all changes since snapshot)
sudo rm /hddRaid1/VirtualMachines/test-audiobook-cachyos.pristine-os-deps-2026-02-22
# Fix potential circular backingStore in XML (virt-xml sometimes leaves stale refs)
sudo virsh dumpxml test-audiobook-cachyos > /tmp/vm-fix.xml
python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('/tmp/vm-fix.xml')
for disk in tree.getroot().iter('disk'):
    for bs in disk.findall('backingStore'):
        disk.remove(bs)
tree.write('/tmp/vm-fix.xml', xml_declaration=True)
"
sudo virsh define /tmp/vm-fix.xml
sudo virsh start test-audiobook-cachyos
# Re-create snapshot after done:
sudo virsh snapshot-create-as test-audiobook-cachyos pristine-os-deps-2026-02-22 \
  "Pristine CachyOS with all audiobook-manager dependencies. No app installed." --disk-only
```

### SPICE Display for UI Testing

```bash
remote-viewer spice://127.0.0.1:5900
virt-viewer --connect qemu:///system test-audiobook-cachyos
```

### What Runs Where

| Test Type | Where | Example |
|-----------|-------|---------|
| Unit tests | Dev machine | `pytest library/tests/test_metadata.py` |
| Config/lint tests | Dev machine | `pytest library/tests/test_config.py` |
| API integration | VM | `pytest library/tests/test_backoffice_integration.py` |
| UI/Playwright | VM | `pytest library/tests/test_player_navigation_persistence.py` |
| Auth/WebAuthn | Dev (unit) / VM (integration) | Unit mocks OK; real auth flow needs VM |
| Auth lifecycle | VM | `pytest library/tests/test_auth_lifecycle_integration.py` |

### Fresh Install on Pristine VM

The VM always starts from a pristine snapshot (no app installed). The `/test` framework
automatically detects this and runs `install.sh --system` before tests. For manual testing:

```bash
# SSH uses claude account (password: REDACTED_VM_PASSWORD, key: ~/.ssh/id_ed25519)
# install.sh creates the audiobooks no-login service user/group that owns the app
scp -i ~/.ssh/id_ed25519 -r . claude@192.168.122.104:/tmp/fresh-install/
ssh -i ~/.ssh/id_ed25519 claude@192.168.122.104 \
  "cd /tmp/fresh-install && sudo ./install.sh --system"
```

### Deploy Updates (after initial install)

```bash
./upgrade.sh --from-project . --remote 192.168.122.104 --yes
# Verify:
ssh -i ~/.ssh/id_ed25519 claude@192.168.122.104 "cat /opt/audiobooks/VERSION"
curl -s http://192.168.122.104:5001/api/system/version
```

## After Syncing Project to Production

After running `upgrade.sh`:
1. Verify all wrapper scripts execute: `for cmd in /usr/local/bin/audiobooks-*; do $cmd --help 2>&1 | head -1 || echo "BROKEN: $cmd"; done`
2. Verify API responds: `curl -s http://localhost:5001/api/system/version`
3. Verify web UI loads and buttons work

## CRITICAL: Test/QA Data Isolation

**No test VM, QA VM, or test/QA Docker container may have LIVE ACCESS (mounts) to production storage.**

Copying production data *into* a test/QA environment is fine — once data is on the VM's own disk, it's fully isolated. The prohibition is against live filesystem links that let test environments read or write production storage directly.

### What's allowed vs forbidden

| Action | Allowed? | Why |
|--------|----------|-----|
| VM creates own fresh DB via `install.sh` | **Yes** | Fully isolated on VM disk |
| `scp`/`rsync` production DB into VM | **Yes** | It's a copy — isolated on VM disk |
| Copy production library into VM disk | **Yes** | Isolated copy, up to ~275GB is fine |
| Mount host production paths via NFS/CIFS/virtiofs | **NEVER** | Live access to production filesystem |
| Docker `-v` mount to host production paths | **NEVER** | Live access to production filesystem |

### What each environment gets

| Environment | Databases | Audiobook Library | Configuration |
|-------------|-----------|-------------------|---------------|
| **Production** (host) | `/var/lib/audiobooks/db/*.db` | `/hddRaid1/Audiobooks/Library/` (full) | `/etc/audiobooks/` |
| **Test VM** | Own DBs on VM disk (fresh or copied) | Own library on VM disk (<275GB) | Own config on VM disk |
| **QA VM** | Own DBs on VM disk (fresh or copied) | Own library on VM disk (<275GB) | Own config on VM disk |
| **Docker test** | Ephemeral in-container DB | Sample data via volume or none | Container env vars only |

### Prohibited actions

- **NEVER** mount `/hddRaid1/Audiobooks/` into a test/QA VM via NFS, CIFS, virtiofs, or virtio-9p
- **NEVER** mount production database paths into a VM or Docker container as a live filesystem
- **NEVER** configure Docker `-v` to bind-mount host production paths at runtime
- **NEVER** give test/QA environments write access to production storage through any mechanism

### Release leak prevention (COPYRIGHT/LICENSE CRITICAL)

Production audiobook files are personally owned and licensed content. Accidentally including them in a release (GitHub, Docker registry, tarball) would expose private data and create copyright/trademark liability.

**Mandatory safeguards:**
- **Docker test containers**: Any production data copied into a test container MUST be cleaned up (container removed) during Phase D cleanup or Phase C, BEFORE `/test` formally ends
- **Docker test images**: NEVER build a Docker image with production data baked in via `COPY`. Use runtime `-v` mounts or `docker cp` for test data — these don't persist in the image
- **Project working tree**: NEVER copy production data (audiobooks, databases, configs) into the project directory. If this happens accidentally, remove it BEFORE any commit or release operation
- **Pre-release guard**: `/git-release` checks for production paths in release artifacts (see separation check in git-release skill). This is the last line of defense.

## Testing & Validation Notes

When running `/test`:
1. **DO NOT** access production data from project code
2. **DO NOT** create symlinks from application to project
3. **DO** use test data in `./library/testdata/`
4. **DO** verify application works independently if project is deleted
5. **DO** use `./upgrade.sh` to update the application, never manual symlinks
