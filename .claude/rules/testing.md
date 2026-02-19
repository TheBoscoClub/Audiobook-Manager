# Testing — VM Isolation & Verification

## ALL Application Testing on test-audiobook-cachyos

**Dev machine is for unit tests and code editing ONLY. All integration, API, UI, and E2E tests MUST run against the dedicated test VM.**

- **Dev machine**: Unit tests, linting, static analysis, code editing
- **VM (test-audiobook-cachyos)**: Integration tests, API tests, UI/Playwright tests, auth tests, E2E tests
- **Before testing**: Always deploy latest code with `./deploy-vm.sh --host 192.168.122.104 --full --restart`

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
| `pristine-deps-2026-02-18` | Clean CachyOS, all app deps (Python 3.14.3, ffmpeg 8.0.1, sqlcipher 4.13.0), kernel 6.19.2, tmpfs /tmp=4G, no app installed | Fresh install testing |

**Revert procedure** (external snapshots):
```bash
sudo virsh destroy test-audiobook-cachyos   # stop VM
# Delete snapshot metadata
sudo virsh snapshot-delete test-audiobook-cachyos pristine-deps-2026-02-18 --metadata
# Remove overlay, point back to base, remove backingStore from XML
sudo virt-xml test-audiobook-cachyos --edit target=vda --disk path=/var/lib/libvirt/images/test-audiobook-cachyos.qcow2
# Fix XML if needed (remove circular backingStore)
sudo rm /var/lib/libvirt/images/test-audiobook-cachyos.pristine-deps-2026-02-18
sudo virsh start test-audiobook-cachyos
# Re-create snapshot after done:
sudo virsh snapshot-create-as test-audiobook-cachyos pristine-deps-2026-02-18 "description" --disk-only
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

### Deploy Before Testing

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
