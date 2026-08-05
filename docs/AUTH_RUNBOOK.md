# Authentication System Operations Runbook

Practical guide for operating and maintaining the secure remote access authentication system.

> **Related Documentation:**
>
> - [README — Authentication Section](../README.md#authentication-v50) — User-facing setup guide
> - [Architecture — Auth Module](ARCHITECTURE.md#authentication-module-architecture) — System design and database schema
> - [Auth Failure Modes](AUTH_FAILURE_MODES.md) — Troubleshooting guide
> - [Secure Remote Access Spec](SECURE_REMOTE_ACCESS_SPEC.md) — Full design specification

## Quick Reference

| Task | Command |
|------|---------|
| Check API status | `systemctl status audiobook-api` |
| View recent logs | `journalctl -u audiobook-api -n 100` |
| Check user count | `./library/tools/auth_admin.py --list-users` |
| Create admin | `./library/tools/auth_admin.py --create-admin USERNAME` (or Back Office → USERS → Create User) |
| Reset user auth | `./library/tools/auth_admin.py --reset-auth USERNAME` (or Back Office → USERS → Reset Credentials) |
| Force logout user | `./library/tools/auth_admin.py --logout USERNAME` |
| Backup database | `./scripts/backup-auth.sh` |
| Check inbox | `./library/tools/inbox_cli.py --list` |
| Send notification | `./library/tools/notify_cli.py --message "TEXT"` |

## Daily Operations

### 1. Health Check

Run daily or add to monitoring:

```bash
#!/bin/bash
# health-check.sh

echo "=== Auth System Health Check ==="

# API responding (unauthenticated health endpoint)
if curl -sf http://localhost:5001/api/system/health > /dev/null; then
    echo "[OK] API is responding"
else
    echo "[FAIL] API not responding"
    exit 1
fi

# Database accessible
if sqlite3 /var/lib/audiobooks/auth.db "SELECT 1;" > /dev/null 2>&1; then
    echo "[OK] Database accessible"
else
    echo "[FAIL] Database not accessible"
    exit 1
fi

# Key file exists
if test -r /etc/audiobooks/auth.key; then
    echo "[OK] Encryption key present"
else
    echo "[FAIL] Encryption key missing"
    exit 1
fi

# Check disk space
DISK_USAGE=$(df -h /var/lib/audiobooks | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USAGE" -lt 90 ]; then
    echo "[OK] Disk usage: ${DISK_USAGE}%"
else
    echo "[WARN] Disk usage high: ${DISK_USAGE}%"
fi

# Count active sessions
SESSION_COUNT=$(sqlite3 /var/lib/audiobooks/auth.db "SELECT COUNT(*) FROM sessions;")
echo "[INFO] Active sessions: $SESSION_COUNT"

# Count users
USER_COUNT=$(sqlite3 /var/lib/audiobooks/auth.db "SELECT COUNT(*) FROM users;")
echo "[INFO] Registered users: $USER_COUNT"

# Unread inbox messages
INBOX_COUNT=$(sqlite3 /var/lib/audiobooks/auth.db "SELECT COUNT(*) FROM inbox WHERE status = 'unread';")
if [ "$INBOX_COUNT" -gt 0 ]; then
    echo "[NOTICE] Unread inbox messages: $INBOX_COUNT"
fi

echo "=== Health Check Complete ==="
```

### 2. Log Review

Check for authentication issues:

```bash
# Failed login attempts in last hour
journalctl -u audiobook-api --since "1 hour ago" | grep -i "login failed\|invalid\|unauthorized"

# Successful logins
journalctl -u audiobook-api --since "1 hour ago" | grep -i "login success\|authenticated"

# Session events
journalctl -u audiobook-api --since "1 hour ago" | grep -i "session"
```

### 3. Inbox Check

Check for user messages:

```bash
# List unread messages
./library/tools/inbox_cli.py --list --unread

# Mark message as read
./library/tools/inbox_cli.py --mark-read MESSAGE_ID

# Reply to message (if in-app)
./library/tools/inbox_cli.py --reply MESSAGE_ID "Your reply here"
```

## User Management

### Create New Admin User

```bash
# Interactive creation
./library/tools/auth_admin.py --create-admin

# Or with username specified
./library/tools/auth_admin.py --create-admin newadmin
```

### Reset User Authentication

When user loses access to their authenticator:

```bash
# Reset auth (user must re-register auth method)
./library/tools/auth_admin.py --reset-auth USERNAME

# Or add magic link as alternative
./library/tools/auth_admin.py --add-magic-link USERNAME user@email.com
```

### Disable User Account

```bash
# Disable without deleting
./library/tools/auth_admin.py --disable USERNAME

# Re-enable later
./library/tools/auth_admin.py --enable USERNAME
```

### Delete User Account

```bash
# Permanently delete (requires confirmation)
./library/tools/auth_admin.py --delete-user USERNAME

# This also:
# - Removes all sessions
# - Removes user positions
# - Removes notification dismissals
# - Logs deletion to audit trail
```

### Force Logout User

```bash
# End all sessions for user
./library/tools/auth_admin.py --logout USERNAME

# Logout all users (emergency)
./library/tools/auth_admin.py --logout-all
```

## Session Lifetimes & Rolling Renewal

Two session flavors, driven by the "Stay logged in" checkbox at login:

| Flavor | Cookie | Server-side lifetime |
|--------|--------|----------------------|
| Standard (unchecked) | Session cookie — cleared on browser close | Removed after 120 min of inactivity (`Session.DEFAULT_GRACE_MINUTES`) |
| Persistent ("remember me") | `Max-Age` 400 days | No inactivity timeout; absolute 400-day `expires_at` horizon, advanced by rolling renewal |

Browsers clamp stored cookies to a **maximum of 400 days** regardless of
`Max-Age` (RFC 6265bis; Chrome 104+, Firefox 108+), so no cookie can outlive
that window unrenewed. Persistent sessions therefore use **rolling renewal**:
on any authenticated request where the session is past half its life
(~200 days remaining), the API re-issues the same cookie with a fresh
400-day `Max-Age` and advances the session row's `expires_at` to match. The
practical result:

- **Active users never expire** — each renewal resets both the browser's
  400-day clock and the server-side horizon
- **Dormant sessions die at 400 days** — on both ends: the browser expires
  the cookie, and the server rejects + deletes the row past `expires_at`
- **Nothing survives "clear browsing data"** — that limit is inherent to
  cookies; the frontend's `localStorage` token + `/auth/session/restore`
  path covers cookie loss while the session row is still within its horizon

Renewal is implemented once in the auth middleware
(`auth_shared._renew_persistent_session_cookie`), so every route inherits it.
The periodic auth cleanup removes non-persistent rows past the inactivity
grace period and persistent rows past their `expires_at` horizon.

### Renewal re-issues the same token — it does not rotate it (deliberate)

A rolling renewal writes the **same** session token back into the cookie with
a fresh `Max-Age`; it does not mint a new one. This is a considered trade-off,
not an oversight, and a security review that flags "no token rotation on
renewal" should be answered with this section:

- Rotating on ordinary read traffic races concurrent tabs. Two tabs share one
  cookie jar; whichever tab's response lands second overwrites the first, and
  any request already in flight with the superseded token 401s — mid-listen,
  on a route the user never touched.
- The token is a server-generated random secret (`secrets.token_urlsafe`) in
  an `HttpOnly` + `Secure` + `SameSite=Lax` cookie, so re-issuing the same
  value adds no meaningful exposure over keeping it in place.
- The value still passes `_SAFE_SESSION_TOKEN_RE` on every write, so the
  renewal path cannot become a cookie-injection vector.

Sessions ARE invalidated (row deleted, so the token dies) on logout, on
"force logout user", on a new login without multi-session enabled, and when
the `expires_at` horizon passes.

### Logout is never renewed

The renewal hook runs `after_app_request`, which includes the logout
response. `Session.invalidate()` marks the in-memory session, and the hook
skips a session whose row it has deleted — otherwise logout would return the
clearing `Set-Cookie` *and* a second `Set-Cookie` re-issuing the token the
user just signed out of, leaving the browser logged in. Every session is also
created with a non-NULL `expires_at`; a NULL horizon made `is_valid()`
unconditionally true and made the hook treat every request as overdue.

## Multi-Session Login (v8.0.1.2+)

By default, each user is limited to one active session (logging in on a new device invalidates the previous session). Admins can enable multi-session login to allow concurrent sessions on multiple devices.

### Global Setting

Navigate to **Back Office → SYSTEM** and toggle the **"Allow multi-session login"** checkbox. This sets the system-wide default.

### Per-User Override

Navigate to **Back Office → USERS**, click the pencil icon on a user row, and use the **multi-session selector**:

| Setting | Behavior |
|---------|----------|
| **Default** | Follows the global system setting |
| **Yes** | Always allows multi-session for this user, regardless of global setting |
| **No** | Always enforces single-session for this user, regardless of global setting |

### API Endpoints

```bash
# Get current system settings (includes allow_multi_session)
curl -b cookies.txt https://localhost:5001/auth/admin/settings

# Enable multi-session globally
curl -b cookies.txt -X PATCH https://localhost:5001/auth/admin/settings \
  -H 'Content-Type: application/json' \
  -d '{"allow_multi_session": true}'

# Set per-user override (via roles endpoint)
curl -b cookies.txt -X PUT https://localhost:5001/auth/admin/users/5/roles \
  -H 'Content-Type: application/json' \
  -d '{"multi_session": "yes"}'  # "default", "yes", or "no"
```

## Web-Based User Management (v7.3+)

The Back Office **USERS** tab provides full user lifecycle management without the command line.

### Creating a User (Admin)

Navigate to **Back Office → USERS → Create User**.

1. Enter username and (optionally) email address.
2. Select the auth method:

   | Auth Method | What happens at creation |
   |-------------|--------------------------|
   | **TOTP** | Server generates a secret; admin is shown a QR code and backup codes to hand to the new user |
   | **Magic Link** | Server generates a one-time enrollment link; admin copies it and sends it to the user out-of-band |
   | **Passkey** | A pending slot is created; user completes passkey enrollment on first login |

3. Click **Create**. The user is active immediately.
4. The action is recorded in the audit log and a notification badge appears for other connected admins.

### Editing a User (Admin)

Click the **pencil icon** on any user row to open the edit panel.

- **Username / Email**: Edit inline and click Save.
- **Roles**: Toggle "Admin" and "Can Download" checkboxes.
- **Auth Method**: Select a new method from the dropdown and click Switch. The old credentials are replaced and the user must re-authenticate.
- **Reset Credentials**: Click Reset to generate fresh TOTP secret, magic link, or clear passkeys (depending on current auth type).

### Deleting a User (Admin)

Click the **trash icon** on the user row and confirm. The system rejects deletion if the target is the only admin account (last-admin guard). Audit log rows referencing the deleted user are preserved with a NULL `actor_id` or `target_user_id`.

### Audit Log

The USERS tab bottom panel shows a paginated audit log of all user management actions.

- **Filter by action**: Use the Action dropdown to narrow to a specific event type.
- **Columns**: Timestamp, actor (admin who performed the action), target user, action, details.
- **New entries**: A badge on the USERS tab indicates unread audit entries since the admin last viewed the log. The badge is pushed in real time via WebSocket to all connected admins.
- **Direct query**: `sqlite3 /var/lib/audiobooks/auth.db "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 20;"`

## Self-Service Account Management (v7.3+)

Authenticated users can manage their own account from the **My Account** button in the shell header (person icon, top right).

| What users can do | Notes |
|-------------------|-------|
| View profile | Username, email, auth type, member-since date |
| Change username | Takes effect immediately; all existing sessions remain valid |
| Change email | Used for magic link and admin notification delivery |
| Switch auth method | Two-step flow: (1) select new method, (2) complete enrollment (scan QR / copy link / register passkey) |
| Reset credentials | Generates fresh credentials for current auth method |
| Delete own account | Requires typing a confirmation phrase; blocked if user is the only admin |

All self-service changes are recorded in the audit log and trigger admin email notifications for critical actions (see below).

## Admin Notifications (v7.3+)

### What triggers a notification

| Action | In-app badge | Admin email |
|--------|-------------|-------------|
| Username change | Yes | Yes |
| Auth method switch | Yes | Yes |
| Credential reset | Yes | Yes |
| Account deletion (self or admin) | Yes | Yes |
| Email update | Audit log only | No |
| Role change | Audit log only | No |

### Email delivery requirements

Admin email alerts are sent to every admin account that has an email address set. Delivery requires valid SMTP configuration in `/etc/audiobooks/audiobooks.conf`:

```ini
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USER=resend
SMTP_PASS=<api-key>
SMTP_FROM=library@YOUR_DOMAIN.example
```

If SMTP is not configured, the action still succeeds and is logged — only the email delivery is skipped (no error raised).

Instead of an inline `SMTP_PASS`, the key can live in a `SMTP_PASS_FILE` pointer file — including the hardened per-start derivation via `derive-service-secret`. See [EMAIL-SETUP.md](EMAIL-SETUP.md) ("Storing the API key in a separate file" and "Hardened setup").

### Clearing the notification badge

Open Back Office → USERS → scroll to the Audit Log panel. Viewing the audit log marks all current entries as read for your session, clearing the badge.

## Notification Management

### Send Global Notification

```bash
# Simple info notification
./library/tools/notify_cli.py --message "New books added to the library!"

# Maintenance notification with timing
./library/tools/notify_cli.py \
    --type maintenance \
    --message "Server maintenance Saturday 2am-4am" \
    --starts "2024-01-20 00:00" \
    --expires "2024-01-21 04:00"

# High priority outage alert
./library/tools/notify_cli.py \
    --type outage \
    --priority 10 \
    --message "Downloads temporarily unavailable"
```

### Send Personal Notification

```bash
# Notification to specific user
./library/tools/notify_cli.py \
    --type personal \
    --target-user USERNAME \
    --message "Your request has been fulfilled!"
```

### Manage Notifications

```bash
# List all notifications
./library/tools/notify_cli.py --list

# Delete expired notifications
./library/tools/notify_cli.py --cleanup

# Delete specific notification
./library/tools/notify_cli.py --delete NOTIFICATION_ID
```

## Backup Procedures

### Manual Backup

```bash
#!/bin/bash
# backup-auth.sh

BACKUP_DIR="/backups/audiobooks/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Stop API briefly for consistent backup
systemctl stop audiobook-api

# Copy database and key
cp /var/lib/audiobooks/auth.db "$BACKUP_DIR/auth.db"
cp /etc/audiobooks/auth.key "$BACKUP_DIR/auth.key"

# Restart API
systemctl start audiobook-api

# Verify backup
if sqlite3 "$BACKUP_DIR/auth.db" -cmd "PRAGMA key = \"x'$(cat $BACKUP_DIR/auth.key)'\"" "SELECT COUNT(*) FROM users;" > /dev/null 2>&1; then
    echo "Backup verified: $BACKUP_DIR"
else
    echo "ERROR: Backup verification failed!"
    exit 1
fi

# Cleanup old backups (keep 30 days)
find /backups/audiobooks -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;
```

### Automated Backup (cron)

```bash
# Add to crontab
0 3 * * * /opt/audiobooks/scripts/backup-auth.sh >> /var/log/audiobooks/backup.log 2>&1
```

### Restore from Backup

```bash
#!/bin/bash
# restore-auth.sh BACKUP_DATE

BACKUP_DATE=${1:-$(ls -1 /backups/audiobooks | tail -1)}
BACKUP_DIR="/backups/audiobooks/$BACKUP_DATE"

echo "Restoring from: $BACKUP_DIR"

# Verify backup exists
if [ ! -f "$BACKUP_DIR/auth.db" ] || [ ! -f "$BACKUP_DIR/auth.key" ]; then
    echo "ERROR: Backup files not found"
    exit 1
fi

# Stop API
systemctl stop audiobook-api

# Backup current state
mv /var/lib/audiobooks/auth.db /var/lib/audiobooks/auth.db.before-restore
mv /etc/audiobooks/auth.key /etc/audiobooks/auth.key.before-restore

# Restore
cp "$BACKUP_DIR/auth.db" /var/lib/audiobooks/auth.db
cp "$BACKUP_DIR/auth.key" /etc/audiobooks/auth.key

# Fix permissions
chown audiobooks:audiobooks /var/lib/audiobooks/auth.*
chmod 600 /var/lib/audiobooks/auth.*

# Start API
systemctl start audiobook-api

echo "Restore complete. Verify by logging in."
```

## Troubleshooting Procedures

### User Cannot Login

1. **Check if user exists:**

   ```bash
   ./library/tools/auth_admin.py --info USERNAME
   ```

2. **Check for active sessions:**

   ```bash
   ./library/tools/auth_admin.py --sessions USERNAME
   ```

3. **Check auth type:**
   - TOTP: Verify server time is correct (`timedatectl`)
   - Passkey: Check domain matches RP ID
   - Magic Link: Check email delivery

4. **Reset if needed:**

   ```bash
   ./library/tools/auth_admin.py --reset-auth USERNAME
   ```

### API Not Starting

1. **Check service status:**

   ```bash
   systemctl status audiobook-api
   journalctl -u audiobook-api -n 50
   ```

2. **Check database accessibility:**

   ```bash
   sqlite3 /var/lib/audiobooks/auth.db "PRAGMA key = \"x'$(cat /etc/audiobooks/auth.key)'\"" "SELECT 1;"
   ```

3. **Check port availability:**

   ```bash
   ss -tlnp | grep 5001
   ```

4. **Check permissions:**

   ```bash
   ls -la /var/lib/audiobooks/
   # Should be owned by audiobooks:audiobooks
   ```

### High Memory/CPU Usage

1. **Check for runaway processes:**

   ```bash
   top -p $(pgrep -f audiobook-api)
   ```

2. **Check session count:**

   ```bash
   sqlite3 /var/lib/audiobooks/auth.db "SELECT COUNT(*) FROM sessions;"
   # If very high, run cleanup:
   ./library/tools/auth_admin.py --cleanup-sessions
   ```

3. **Check database size:**

   ```bash
   ls -lh /var/lib/audiobooks/auth.db*
   # If WAL file is huge:
   sqlite3 /var/lib/audiobooks/auth.db "PRAGMA wal_checkpoint(TRUNCATE);"
   ```

### SSL Certificate Issues

1. **Check certificate status:**

   ```bash
   curl -vI https://your-domain.com 2>&1 | grep -A5 "SSL certificate"
   ```

2. **Renew with Caddy:**

   ```bash
   # Caddy auto-renews, but you can force:
   caddy reload --config /etc/caddy/Caddyfile
   ```

## Security Incident Response

### Suspected Unauthorized Access

1. **Immediately disable affected account:**

   ```bash
   ./library/tools/auth_admin.py --disable USERNAME
   ```

2. **Force logout all sessions:**

   ```bash
   ./library/tools/auth_admin.py --logout USERNAME
   ```

3. **Review recent activity:**

   ```bash
   journalctl -u audiobook-api --since "24 hours ago" | grep -i "USERNAME"
   ```

4. **Check for unusual patterns:**

   ```bash
   # Multiple failed logins
   journalctl -u audiobook-api | grep "login failed" | tail -50

   # Unusual IPs
   sqlite3 /var/lib/audiobooks/auth.db \
     "SELECT ip_address, COUNT(*) FROM sessions GROUP BY ip_address ORDER BY COUNT(*) DESC;"
   ```

### Suspected Data Breach

1. **Preserve evidence:**

   ```bash
   cp /var/lib/audiobooks/auth.db /var/lib/audiobooks/auth.db.evidence
   journalctl -u audiobook-api > /var/log/audiobooks/incident-$(date +%s).log
   ```

2. **Rotate encryption key (nuclear option):**

   ```bash
   # This invalidates ALL sessions and requires DB migration
   # Only do this if key compromise is confirmed
   # Contact security team first
   ```

3. **Force password reset for all users:**

   ```bash
   ./library/tools/auth_admin.py --reset-all-auth
   ```

## Maintenance Windows

### Planned Maintenance

```bash
# 1. Send advance notification
./library/tools/notify_cli.py \
    --type maintenance \
    --priority 5 \
    --message "Scheduled maintenance Saturday 2-4am EST. Downloads will be unavailable." \
    --starts "2024-01-19 00:00" \
    --expires "2024-01-20 04:00"

# 2. During maintenance window:
systemctl stop audiobook-api

# 3. Perform maintenance tasks

# 4. Start services
systemctl start audiobook-api

# 5. Verify
curl -sf http://localhost:5001/api/health

# 6. Remove maintenance notification
./library/tools/notify_cli.py --delete NOTIFICATION_ID
```

### Emergency Maintenance

```bash
# 1. Send immediate notification
./library/tools/notify_cli.py \
    --type outage \
    --priority 10 \
    --message "Emergency maintenance in progress. Service will be restored shortly."

# 2. Perform emergency work

# 3. Update notification when done
./library/tools/notify_cli.py --delete NOTIFICATION_ID
./library/tools/notify_cli.py \
    --message "Service restored. Thank you for your patience."
```

## Metrics and Reporting

### User Statistics

```bash
# Total users
sqlite3 /var/lib/audiobooks/auth.db "SELECT COUNT(*) FROM users;"

# Users by auth type
sqlite3 /var/lib/audiobooks/auth.db \
    "SELECT auth_type, COUNT(*) FROM users GROUP BY auth_type;"

# Admin count
sqlite3 /var/lib/audiobooks/auth.db \
    "SELECT COUNT(*) FROM users WHERE is_admin = 1;"

# Users who can download
sqlite3 /var/lib/audiobooks/auth.db \
    "SELECT COUNT(*) FROM users WHERE can_download = 1;"
```

### Activity Statistics

```bash
# Active sessions
sqlite3 /var/lib/audiobooks/auth.db "SELECT COUNT(*) FROM sessions;"

# Sessions by age
sqlite3 /var/lib/audiobooks/auth.db \
    "SELECT
        CASE
            WHEN last_seen > datetime('now', '-1 hour') THEN 'Last hour'
            WHEN last_seen > datetime('now', '-1 day') THEN 'Last day'
            ELSE 'Older'
        END as age,
        COUNT(*)
    FROM sessions GROUP BY age;"

# Recent logins
sqlite3 /var/lib/audiobooks/auth.db \
    "SELECT created_at, user_agent FROM sessions ORDER BY created_at DESC LIMIT 10;"
```

### Contact Statistics

```bash
# Unread messages
sqlite3 /var/lib/audiobooks/auth.db \
    "SELECT COUNT(*) FROM inbox WHERE status = 'unread';"

# Messages by status
sqlite3 /var/lib/audiobooks/auth.db \
    "SELECT status, COUNT(*) FROM inbox GROUP BY status;"

# Active notifications
sqlite3 /var/lib/audiobooks/auth.db \
    "SELECT type, COUNT(*) FROM notifications
     WHERE (starts_at IS NULL OR starts_at <= datetime('now'))
     AND (expires_at IS NULL OR expires_at > datetime('now'))
     GROUP BY type;"
```
