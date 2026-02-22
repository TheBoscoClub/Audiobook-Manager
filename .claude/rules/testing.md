# Testing — VM Isolation & Verification

## ALL Application Testing on test-audiobook-cachyos

**Dev machine is for unit tests and code editing ONLY. All integration, API, UI, and E2E tests MUST run against the dedicated test VM.**

- **Dev machine**: Unit tests, linting, static analysis, code editing
- **VM (test-audiobook-cachyos)**: Integration tests, API tests, UI/Playwright tests, auth tests, E2E tests
- **Before testing on pristine VM**: Run `./install.sh --system` first (creates audiobooks user/group, dirs, venv, DB, services), then deploy
- **Before testing on installed VM**: Deploy latest code with `./deploy-vm.sh --host 192.168.122.104 --full --restart`
- **`/test` handles this automatically**: Phase VM-lifecycle detects pristine state and auto-installs before tests run

### VM Connection Details

| Property | Value |
|----------|-------|
| Hostname | `test-audiobook-cachyos` / `192.168.122.104` |
| SSH user | `claude` |
| SSH key | `~/.claude/ssh/id_ed25519` |
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

**Revert procedure** (external snapshots):
```bash
sudo virsh destroy test-audiobook-cachyos   # stop VM if running
# Delete snapshot metadata
sudo virsh snapshot-delete test-audiobook-cachyos pristine-os-deps-2026-02-22 --metadata
# Commit overlay into base, then repoint VM to base
sudo qemu-img commit /var/lib/libvirt/images/test-audiobook-cachyos.pristine-os-deps-2026-02-22
sudo virt-xml test-audiobook-cachyos --edit target=vda --disk path=/var/lib/libvirt/images/test-audiobook-cachyos.qcow2
# Remove overlay file
sudo rm /var/lib/libvirt/images/test-audiobook-cachyos.pristine-os-deps-2026-02-22
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
# SSH uses claude account (password: Claud3Cod3, key: ~/.claude/ssh/id_ed25519)
# install.sh creates the audiobooks no-login service user/group that owns the app
scp -i ~/.claude/ssh/id_ed25519 -r . claude@192.168.122.104:/tmp/fresh-install/
ssh -i ~/.claude/ssh/id_ed25519 claude@192.168.122.104 \
  "cd /tmp/fresh-install && sudo ./install.sh --system"
```

### Deploy Updates (after initial install)

```bash
./deploy-vm.sh --host 192.168.122.104 --full --restart
# Verify:
ssh -i ~/.claude/ssh/id_ed25519 claude@192.168.122.104 "cat /opt/audiobooks/VERSION"
curl -s http://192.168.122.104:5001/api/system/version
```

## After Syncing Project to Production

After running `upgrade.sh` or `deploy.sh`:
1. Verify all wrapper scripts execute: `for cmd in /usr/local/bin/audiobooks-*; do $cmd --help 2>&1 | head -1 || echo "BROKEN: $cmd"; done`
2. Verify API responds: `curl -s http://localhost:5001/api/system/version`
3. Verify web UI loads and buttons work

## Testing & Validation Notes

When running `/test`:
1. **DO NOT** access production data from project code
2. **DO NOT** create symlinks from application to project
3. **DO** use test data in `./library/testdata/`
4. **DO** verify application works independently if project is deleted
5. **DO** use `./deploy.sh` to update the application, never manual symlinks
