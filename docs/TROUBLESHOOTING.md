# Troubleshooting Guide

Quick reference for diagnosing and resolving common Audiobook-Manager issues.

**Related docs**: [AUTH_FAILURE_MODES.md](AUTH_FAILURE_MODES.md) (auth-specific), [AUTH_RUNBOOK.md](AUTH_RUNBOOK.md) (admin procedures), [ARCHITECTURE.md](ARCHITECTURE.md) (system design)

---

## Table of Contents

1. [Service Startup Failures](#1-service-startup-failures)
2. [Port Conflicts](#2-port-conflicts)
3. [Database Issues](#3-database-issues)
4. [Authentication Failures](#4-authentication-failures)
5. [Missing Runtime Directories](#5-missing-runtime-directories-after-reboot)
6. [Python venv Broken](#6-python-virtual-environment-broken)
7. [Conversion Queue Stalled](#7-conversion-queue-stalled)
8. [Network Storage Boot Failures](#8-network-storage--hdd-boot-failures)
9. [SSL Certificate Warnings](#9-ssl-certificate-warnings)
10. [Permission Denied Errors](#10-permission-denied-errors)
11. [Health Check Script](#11-health-check-script)
12. [Maintenance Scheduling](#12-maintenance-scheduling)
13. [Streaming Translation & UID/GID Issues](#13-streaming-translation--uidgid-issues-v837)

---

## 1. Service Startup Failures

**Symptoms**: Services fail at boot, `systemctl status audiobook-api` shows errors

```bash
# Check service status
sudo systemctl status audiobook-api

# View recent logs
journalctl -u audiobook-api -n 50

# View all audiobook services
sudo systemctl status 'audiobook*'
```

**Common causes** (check in order):

1. Port already in use → see [Port Conflicts](#2-port-conflicts)
2. Missing `/tmp` directories → see [Missing Runtime Directories](#5-missing-runtime-directories-after-reboot)
3. Data mount not ready → see [Network Storage Boot Failures](#8-network-storage--hdd-boot-failures)
4. Broken Python venv → see [Python venv Broken](#6-python-virtual-environment-broken)

---

## 2. Port Conflicts

**Symptoms**: `Address already in use`, API unreachable

| Port | Service | Purpose |
|------|---------|---------|
| 5001 | audiobook-api | REST API |
| 8443 | audiobook-proxy | HTTPS web UI |
| 8080 | redirect server | HTTP → HTTPS redirect |

```bash
# Find what's using the port
ss -tlnp | grep -E "5001|8443|8080"

# Kill stuck process (if safe)
sudo fuser -k 5001/tcp

# Restart
sudo systemctl restart audiobook-api
```

---

## 3. Database Issues

### Library Database (SQLite)

**Symptoms**: API returns 500 errors, stats show 0 books

```bash
# Verify database exists
ls -la "$(grep AUDIOBOOKS_DATABASE /etc/audiobooks/audiobooks.conf | cut -d= -f2)"

# Test readability
sqlite3 /var/lib/audiobooks/db/audiobooks.db "SELECT COUNT(*) FROM audiobooks;"

# If locked
sqlite3 /var/lib/audiobooks/db/audiobooks.db "PRAGMA wal_checkpoint(TRUNCATE);"

# Vacuum (shrink and optimize)
curl -X POST http://localhost:5001/api/utilities/vacuum
```

### Auth Database (SQLCipher)

**Symptoms**: `file is not a database`, all auth fails, login returns 500

```bash
# Verify both files exist
ls -la /var/lib/audiobooks/auth.db
ls -la /etc/audiobooks/auth.key

# Key must be: 64 hex chars, owned by audiobooks:audiobooks, mode 0600
wc -c /etc/audiobooks/auth.key    # Should be 64
stat -c '%U:%G %a' /etc/audiobooks/auth.key  # Should be audiobooks:audiobooks 600

# Test database with key
sqlite3 /var/lib/audiobooks/auth.db \
  -cmd "PRAGMA key = \"x'$(cat /etc/audiobooks/auth.key)'\"" \
  "SELECT COUNT(*) FROM users;"
```

**CRITICAL**: Always backup both `auth.db` AND `auth.key` together. Losing the key = permanent data loss — the database cannot be decrypted without it.

---

## 4. Authentication Failures

| Problem | Diagnosis | Solution |
|---------|-----------|----------|
| TOTP code rejected | Clock skew > 30 seconds | `sudo timedatectl set-ntp true` |
| Passkey not recognized | Device credential deleted or domain changed | Admin resets user's auth method |
| Login loops back to login page | Browser blocking cookies, or HTTP without Secure flag | Use HTTPS; check cookie settings |
| All admins locked out | No admin can log in | See emergency recovery below |
| Session expires immediately | Server clock wrong or cookie domain mismatch | Check `timedatectl` and `AUDIOBOOKS_HOSTNAME` |

### Emergency Admin Recovery

When all admins are locked out:

```bash
# Stop the API
sudo systemctl stop audiobook-api

# Create emergency admin via CLI
/opt/audiobooks/library/venv/bin/python \
  /opt/audiobooks/library/auth/cli.py create-admin emergency_admin

# Restart API
sudo systemctl start audiobook-api

# Log in as emergency_admin, fix accounts, then delete the emergency user
```

For detailed auth troubleshooting, see [AUTH_FAILURE_MODES.md](AUTH_FAILURE_MODES.md).

---

## 5. Missing Runtime Directories After Reboot

**Symptoms**: Services fail with `No such file or directory` after reboot. Converter shows "idle" but files stuck in queue.

**Cause**: `/tmp` is tmpfs (RAM-based), cleared on reboot. Required directories must be recreated.

```bash
# Verify tmpfiles.d config
cat /etc/tmpfiles.d/audiobooks.conf

# If missing, create it:
sudo tee /etc/tmpfiles.d/audiobooks.conf <<'EOF'
d /tmp/audiobook-staging    0775 audiobooks audiobooks -
d /tmp/audiobook-triggers   0755 audiobooks audiobooks -
EOF

# Apply now
sudo systemd-tmpfiles --create /etc/tmpfiles.d/audiobooks.conf

# Verify
ls -la /tmp/audiobook-staging /tmp/audiobook-triggers
```

Also verify persistent runtime directories:

```bash
ls -la /var/lib/audiobooks/.run
ls -la /var/lib/audiobooks/.control
```

---

## 6. Python Virtual Environment Broken

**Symptoms**: `ModuleNotFoundError`, service ExecStart fails, import errors

**Cause**: Broken symlinks (common after `rsync` deployments or Python version upgrades)

```bash
# CORRECT validation (actually tests if venv works)
/opt/audiobooks/library/venv/bin/python --version

# WRONG validation (rsync copies broken symlinks as dirs)
# [[ -d /opt/audiobooks/library/venv ]]  ← DON'T USE THIS

# If broken, rebuild
sudo -u audiobooks bash -c '
  rm -rf /opt/audiobooks/library/venv
  python3 -m venv /opt/audiobooks/library/venv
  /opt/audiobooks/library/venv/bin/pip install -r /opt/audiobooks/library/requirements.txt
'
sudo systemctl restart audiobook-api
```

---

## 7. Conversion Queue Stalled

**Symptoms**: Converter idle, files not processing, 0% progress in Back Office

```bash
# Check converter status
sudo systemctl status audiobook-converter
journalctl -u audiobook-converter -n 50

# Check disk space (conversions need /tmp space)
df -h /tmp /srv/audiobooks

# Verify staging directory exists
ls -la /tmp/audiobook-staging

# Check queue
cat /srv/audiobooks/.index/queue.txt 2>/dev/null | wc -l

# Restart converter
sudo systemctl restart audiobook-converter
```

**If disk full**: Clear stale staging files:

```bash
# Check what's in staging (old failed conversions)
ls -la /tmp/audiobook-staging/

# Remove stale entries older than 7 days
find /tmp/audiobook-staging -maxdepth 2 -mtime +7 -type f -delete
```

---

## 8. Network Storage / HDD Boot Failures

**Symptoms**: Services fail at boot but recover after a few minutes. Log shows:

```text
Failed at step NAMESPACE spawning /bin/sh: No such file or directory
```

**Cause**: Data directory (NFS, SMB, secondary HDD) not mounted when service starts.

**Fix**: Tell systemd to wait for the mount:

```bash
sudo systemctl edit --full audiobook-api.service
```

Add to `[Unit]` section:

```ini
# For local storage (HDD, BTRFS subvolume)
RequiresMountsFor=/opt/audiobooks /path/to/audiobooks

# For network storage, also add:
After=network-online.target remote-fs.target
Wants=network-online.target
```

Then reload:

```bash
sudo systemctl daemon-reload
sudo systemctl restart audiobook-api
```

---

## 9. SSL Certificate Warnings

**Expected**: Browser shows "Your connection is not private" on first visit. This is normal for self-signed certificates.

**Fix for local access**: Click "Advanced" → "Proceed to localhost (unsafe)". Browser remembers the exception.

**Fix for production**: Use a reverse proxy with real certificates:

```bash
# Example with Caddy (auto Let's Encrypt)
# /etc/caddy/Caddyfile
audiobooks.example.com {
    reverse_proxy localhost:5001
}
```

**If certificate files are missing**:

```bash
# Regenerate self-signed cert
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout /etc/audiobooks/certs/server.key \
  -out /etc/audiobooks/certs/server.crt \
  -days 365 -subj "/CN=localhost"
sudo chown audiobooks:audiobooks /etc/audiobooks/certs/*
sudo systemctl restart audiobook-proxy
```

---

## 10. Permission Denied Errors

**Symptoms**: `Permission denied` in logs, API returns 500 on write operations

```bash
# Check key directories
stat -c '%U:%G %a %n' \
  /var/lib/audiobooks \
  /var/lib/audiobooks/auth.db \
  /etc/audiobooks/auth.key \
  /tmp/audiobook-staging \
  /opt/audiobooks/library

# Expected ownership: audiobooks:audiobooks
# Fix if wrong
sudo chown -R audiobooks:audiobooks /var/lib/audiobooks
sudo chown audiobooks:audiobooks /etc/audiobooks/auth.key
sudo chmod 600 /etc/audiobooks/auth.key
sudo chmod 775 /tmp/audiobook-staging /tmp/audiobook-triggers
```

**If using ProtectSystem=strict** (systemd security hardening): Only paths listed in `ReadWritePaths=` are writable. Check the service file if a new path needs write access.

---

## 11. Health Check Script

Quick diagnostic script to check system health:

```bash
#!/bin/bash
echo "=== Audiobook-Manager Health Check ==="

# API responding
if curl -sf http://localhost:5001/api/system/health > /dev/null 2>&1; then
  echo "[OK] API responding"
else
  echo "[FAIL] API not responding on port 5001"
fi

# Services running
for svc in audiobook-api audiobook-proxy; do
  if systemctl is-active --quiet $svc; then
    echo "[OK] $svc running"
  else
    echo "[FAIL] $svc not running"
  fi
done

# Database accessible
if sqlite3 /var/lib/audiobooks/db/audiobooks.db "SELECT 1;" > /dev/null 2>&1; then
  echo "[OK] Library database accessible"
else
  echo "[FAIL] Library database inaccessible"
fi

# Auth key exists
if [[ -r /etc/audiobooks/auth.key ]]; then
  echo "[OK] Auth key readable"
else
  echo "[FAIL] Auth key missing or unreadable"
fi

# Runtime directories
for dir in /tmp/audiobook-staging /tmp/audiobook-triggers; do
  if [[ -d $dir ]]; then
    echo "[OK] $dir exists"
  else
    echo "[FAIL] $dir missing (run: sudo systemd-tmpfiles --create)"
  fi
done

# Disk space
used=$(df --output=pcent /var/lib/audiobooks 2>/dev/null | tail -1 | tr -d '% ')
if [[ $used -lt 90 ]]; then
  echo "[OK] Disk usage: ${used}%"
else
  echo "[WARN] Disk usage: ${used}% (above 90% threshold)"
fi

echo "=== Done ==="
```

---

## 12. Maintenance Scheduling

### Scheduler Not Running

```bash
# Check service status
systemctl status audiobook-scheduler

# Check logs
journalctl -u audiobook-scheduler -f

# Verify lock file isn't stale
ls -la $AUDIOBOOKS_RUN_DIR/maintenance.lock
```

### WebSocket Not Connecting

```bash
# Verify Gunicorn worker type
ps aux | grep gunicorn
# Should show: -k gevent (NOT GeventWebSocketWorker — that causes double 101)

# Check proxy tunneling
curl -v -H "Connection: Upgrade" -H "Upgrade: websocket" https://localhost:8443/api/ws
```

### Announcements Not Appearing

```bash
# Check notification queue
sqlite3 /path/to/audiobooks.db "SELECT * FROM maintenance_notifications WHERE delivered = 0"

# Check active announcements
curl http://localhost:5001/api/maintenance/announcements
```

### Knife Switch No Sound

Web Audio API requires user interaction with the page before audio can play (browser autoplay policy). Click anywhere on the page first, then toggle the knife switch.

### Scheduled Windows Not Executing

```bash
# Check for stale lock
ls -la $AUDIOBOOKS_RUN_DIR/maintenance.lock

# Check cron expression is valid
python3 -c "from croniter import croniter; print(croniter('0 3 * * 0').get_next())"

# Check window is enabled
sqlite3 /path/to/audiobooks.db "SELECT id, name, enabled, cron_expression FROM maintenance_windows"
```

---

## 13. Streaming Translation & UID/GID Issues (v8.3.7+)

### Docker container cold-boot takes 30–45 minutes on first start

**Symptoms**: First `docker run` / `docker compose up` after upgrade sits on
"Initializing scanner…" for 30–45 minutes against a ~2,000-book library.
Subsequent restarts re-do the same work every time.

**Root cause**: UID/GID mismatch between the container's `audiobooks` user
and the host's `audiobooks` user. The container treats host-owned files as
alien because of the UID delta — on every restart the Dockerfile's init path
re-chowns, re-indexes, or attempts scanner initialization against files it
thinks don't belong to it. Pre-8.3.7 installs picked free system UIDs per
distro (prod=935, QA/dev=951, container=1000) so host and container never
agreed.

**Fix**: realign the host to the canonical UID=935 / GID=934 that the
Dockerfile now hardcodes.

```bash
# Preview what will change
sudo bash /opt/audiobooks/scripts/migrate-audiobooks-uid.sh --dry-run

# Apply
sudo bash /opt/audiobooks/scripts/migrate-audiobooks-uid.sh
```

The helper stops `audiobook.target`, runs `usermod -u` + `groupmod -g`,
`chown -R` every path under `/opt/audiobooks`, `/etc/audiobooks`,
`/var/lib/audiobooks`, `/srv/audiobooks` from the old UID/GID to 935/934,
then restarts services. Idempotent — no-op on hosts already at canonical.

### "字幕生成失败" / "Subtitle generation failed" toast on every first book-open (zh-Hans)

**Symptoms**: A zh-Hans (or other non-English) patron opens any book they
haven't listened to before and gets an immediate red error toast. Retrying
does nothing. The book plays fine in English.

**Root cause**: Stale `translation_queue` rows from the legacy batch-pipeline
era (pre-streaming). The queue accumulated `failed` rows with
`"No STT provider configured"` that `subtitles.js::renderGenStatus` still
surfaced through its `phase === "error"` branch.

**Fix**: already applied in v8.3.7 — `queue.py::get_book_translation_status`
collapses non-English `pending` / `processing` / `failed` rows to
`state: "deferred"`, which the UI handlers now render as no-op. If the toast
still appears after upgrade, verify you are running 8.3.7+:

```bash
curl -sk https://localhost:8443/api/system/version
```

### Bilingual transcript panel snaps back to playhead while you're reading

**Symptoms**: Open the transcript side panel, scroll forward (or back) to
read ahead / re-read a passage, and the panel auto-scrolls back to the
current playhead on the next `timeupdate` — about once a second.

**Fix**: v8.3.7 adds a 4-second user-scroll pause. Any
`touchstart` / `wheel` / `pointerdown` inside `#transcript-content` stamps
a `_userScrolledAt` timestamp; `highlightTranscriptCue` refuses to
auto-scroll for `USER_SCROLL_PAUSE_MS` (4 000 ms) after. If this is still
happening post-upgrade, hard-refresh to defeat browser/CDN cache on
`subtitles.js?v=1776891943`. Cachebust enforcement is Task #51 (automated
stamp bump in `upgrade.sh`).

### In-flight subtitle track never appears while a chapter is streaming

**Symptoms**: You start playback on an untranslated book. Audio streams
fine. `subtitles.js` polls the manifest but the chapter never shows up
until streaming finishes and chapter consolidation runs.

**Root cause**: Pre-8.3.7 the subtitle manifest only listed
`chapter_subtitles` rows written at end-of-chapter consolidation.

**Fix**: v8.3.7's in-flight VTT stitching. The manifest now unions
`chapter_subtitles` with a live index of `streaming_segments` rows; the
fall-through route stitches a VTT from completed segments. Verify:

```bash
# Expect the chapter to appear in the manifest as soon as the first
# streaming segment completes (state='completed' in streaming_segments).
curl -sk "https://localhost:8443/api/audiobooks/<id>/subtitles" | jq .
```

If chapters still don't appear, confirm the streaming worker is running
(`systemctl status audiobook-stream-translate`) and the segments are
landing (`sqlite3 audiobooks.db "SELECT state, COUNT(*) FROM streaming_segments WHERE audiobook_id=<id> GROUP BY state"`).

### `audiobook-translations import` drops most streaming segments

**Symptoms**: You export translations from QA with
`audiobook-translations export` and import into prod; streaming segments
are mostly missing from prod's `streaming-audio` directory — 1,465 exported
rows become ~232 extracted files.

**Root cause**: Pre-8.3.7 `transfer.py` used flat arcnames in the tar
(`audio/streaming/{basename}`). Every book's `seg0000.webm` /
`seg0001.webm` overwrote earlier entries.

**Fix**: v8.3.7 nests arcnames by `(audiobook_id, chapter_index, locale, segment_index)`. Re-run export from a 8.3.7+ source; import on 8.3.7+ accepts both the new nested format and the legacy flat format (backwards-compatible for in-flight tarballs).

---

## Quick Reference

| Issue | First Check | Fix |
|-------|-------------|-----|
| API unreachable | `ss -tlnp \| grep 5001` | Kill conflicting process, restart service |
| Auth errors | `ls -la /etc/audiobooks/auth.key` | Verify key exists, 64 hex chars, mode 0600 |
| Post-reboot failures | `ls /tmp/audiobook-staging` | `sudo systemd-tmpfiles --create` |
| Import errors | `venv/bin/python --version` | Rebuild venv |
| Conversion stuck | `df -h /tmp` | Free disk space, restart converter |
| HDD mount timing | `journalctl -u audiobook-api` | Add `RequiresMountsFor` to service |
| Clock skew | `timedatectl` | `sudo timedatectl set-ntp true` |
| Docker 45-min cold boot | `id audiobooks` (host vs container) | `sudo bash scripts/migrate-audiobooks-uid.sh` |
| 字幕生成失败 on every first-open | `curl .../api/system/version` | Upgrade to 8.3.7+ |
| Transcript snaps back while reading | Hard-refresh `subtitles.js?v=` | Upgrade to 8.3.7+ |
| Streaming segments lost on transfer | Re-export from 8.3.7+ source | `audiobook-translations export` (nested format) |
