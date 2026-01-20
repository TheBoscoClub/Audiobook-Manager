# Authentication System Failure Modes

This document describes failure modes in the secure remote access authentication system, their symptoms, impacts, and recovery procedures.

## Database Failures

### 1. Auth Database Unavailable

**Symptoms:**
- HTTP 500 errors on all auth endpoints
- Login/registration completely fails
- Existing sessions cannot be validated

**Cause:**
- `auth.db` file missing or corrupted
- Disk full preventing writes
- File permissions changed

**Impact:**
- All users locked out
- No new logins possible
- Library browsing may work (if not requiring auth)

**Recovery:**
```bash
# Check if database exists
ls -la /var/lib/audiobooks/auth.db

# If corrupted, restore from backup
cp /backups/audiobooks/auth-backup.db /var/lib/audiobooks/auth.db

# Verify permissions
chown audiobooks:audiobooks /var/lib/audiobooks/auth.db
chmod 600 /var/lib/audiobooks/auth.db

# Restart API
systemctl restart audiobook-api
```

### 2. Encryption Key Missing/Wrong

**Symptoms:**
- Database operations fail with "file is not a database" or "SQLCipher: key is incorrect"
- API startup fails silently or with cryptic errors

**Cause:**
- `auth.key` file deleted or corrupted
- Key file has wrong permissions
- Backup restored without matching key

**Impact:**
- Complete auth system failure
- All user data inaccessible
- Cannot decrypt existing credentials

**Recovery:**
```bash
# Check key file exists
ls -la /var/lib/audiobooks/auth.key

# If key is lost permanently:
# 1. Create new database (ALL USER DATA LOST)
rm /var/lib/audiobooks/auth.db
systemctl restart audiobook-api  # Creates fresh DB

# If restoring from backup, restore BOTH files:
cp /backups/audiobooks/auth-backup.db /var/lib/audiobooks/auth.db
cp /backups/audiobooks/auth-backup.key /var/lib/audiobooks/auth.key
chmod 600 /var/lib/audiobooks/auth.*
chown audiobooks:audiobooks /var/lib/audiobooks/auth.*
```

**Prevention:**
- Always backup both `auth.db` AND `auth.key` together
- Keep key file in multiple secure locations
- Test restore procedure periodically

## Session Failures

### 3. Session Token Expired

**Symptoms:**
- User sees "Session expired" message
- Redirected to login page unexpectedly
- API returns 401 Unauthorized

**Cause:**
- Session exceeded configured timeout (default 30 min inactivity)
- Session explicitly logged out
- Server restarted

**Impact:**
- Single user affected
- Normal behavior - not a failure

**Recovery:**
- User logs in again
- No administrator action needed

### 4. Session Cookie Not Set

**Symptoms:**
- Login succeeds but user redirected back to login
- Authentication doesn't persist across requests

**Cause:**
- Browser blocking cookies
- Incorrect SameSite/Secure cookie settings
- HTTP instead of HTTPS (cookies marked Secure won't be sent)

**Impact:**
- Affected users cannot maintain logged-in state

**Recovery:**
```bash
# Verify Caddy/reverse proxy is using HTTPS
curl -I https://your-domain.com/api/auth/session

# Check cookie attributes in response:
# Set-Cookie: auth_token=...; HttpOnly; Secure; SameSite=Lax; Path=/

# If using HTTP in development, use is_dev=True
# which allows non-Secure cookies
```

### 5. Session Database Corruption

**Symptoms:**
- Some users suddenly logged out
- Session validation errors in logs

**Cause:**
- Crash during session write
- Concurrent access issues (rare with SQLite WAL mode)

**Impact:**
- Affected users need to log in again
- No data loss for user accounts

**Recovery:**
```bash
# Sessions table can be safely truncated
# Users just need to log in again
sqlite3 /var/lib/audiobooks/auth.db "DELETE FROM sessions;"

# Or let stale session cleanup handle it automatically
# (runs every 30 minutes via API background task)
```

## Authentication Failures

### 6. TOTP Code Rejected

**Symptoms:**
- User enters 6-digit code, gets "Invalid code"
- Repeated failures

**Cause:**
- Server/device clock skew > 30 seconds
- User entering old code
- Wrong secret provisioned

**Impact:**
- User cannot log in via TOTP

**Recovery:**
```bash
# Check server time
timedatectl

# If time is wrong:
sudo timedatectl set-ntp true
sudo systemctl restart systemd-timesyncd

# Admin can reset user's auth method:
./library/tools/auth_admin.py --reset-auth USERNAME

# User re-registers with new TOTP secret
```

### 7. Passkey Authentication Fails

**Symptoms:**
- Passkey prompt doesn't appear
- "Authenticator not recognized" error

**Cause:**
- Passkey deleted from user's device
- Domain mismatch (RP ID changed)
- Browser/device doesn't support WebAuthn

**Impact:**
- User cannot log in with passkey

**Recovery:**
```bash
# Admin can add alternative auth method:
./library/tools/auth_admin.py --add-magic-link USERNAME email@example.com

# Or reset to allow re-registration:
./library/tools/auth_admin.py --reset-auth USERNAME
```

### 8. Magic Link Email Not Delivered

**Symptoms:**
- User clicks "Send magic link"
- Shows success but email never arrives
- Nothing in spam folder

**Cause:**
- SMTP server configuration wrong
- Email in blocklist/spam filter
- Rate limiting by email provider

**Impact:**
- Users relying on magic link cannot log in

**Recovery:**
```bash
# Check SMTP configuration
grep SMTP /etc/audiobooks/audiobooks.conf

# Test email delivery
./library/tools/test_email.py test@example.com

# View email logs
journalctl -u audiobook-api | grep -i "email\|smtp\|magic"

# Workaround: admin generates magic link manually
./library/tools/auth_admin.py --generate-magic-link USERNAME
# Gives URL to share with user directly
```

## Network/Proxy Failures

### 9. Reverse Proxy Misconfiguration

**Symptoms:**
- 502 Bad Gateway errors
- API unreachable from outside
- Works internally but not externally

**Cause:**
- Caddy/nginx configuration error
- Wrong upstream port
- SSL certificate expired

**Impact:**
- All external users affected

**Recovery:**
```bash
# Check Caddy status
systemctl status caddy
journalctl -u caddy -n 50

# Verify API is listening
curl -s http://localhost:5001/api/health

# Reload Caddy config
systemctl reload caddy

# Check SSL certificate
caddy validate --config /etc/caddy/Caddyfile
```

### 10. Rate Limiting Triggered

**Symptoms:**
- HTTP 429 Too Many Requests
- Users temporarily blocked
- Legitimate users affected by shared IP

**Cause:**
- Brute force protection triggered
- Legitimate high traffic
- Crawler/bot activity

**Impact:**
- Affected IP addresses temporarily blocked

**Recovery:**
```bash
# Rate limits are at Caddy level
# Check Caddy rate limit configuration
grep -A5 "rate_limit" /etc/caddy/Caddyfile

# No persistent blocklist - wait for timeout
# Default: 10 requests per minute per IP

# For persistent attackers, add firewall rules:
sudo ufw deny from ATTACKER_IP
```

## Concurrent Access Issues

### 11. Simultaneous Login Race Condition

**Symptoms:**
- User logs in on two devices rapidly
- One device unexpectedly logged out

**Cause:**
- Single-session-per-user design (intentional security feature)
- New login invalidates existing session

**Impact:**
- Normal behavior - not a failure
- User understands only one session allowed

**Recovery:**
- No action needed - this is expected behavior
- Users log in on the device they want to use

### 12. Database Lock Contention

**Symptoms:**
- Slow authentication responses
- Occasional timeout errors
- "database is locked" errors in logs

**Cause:**
- Many concurrent auth operations
- Long-running transactions
- WAL checkpoint blocking

**Impact:**
- Degraded performance for all users

**Recovery:**
```bash
# Check for stuck transactions
sqlite3 /var/lib/audiobooks/auth.db ".timeout 5000" "PRAGMA wal_checkpoint;"

# If database is locked:
systemctl restart audiobook-api

# For persistent issues, check WAL size:
ls -la /var/lib/audiobooks/auth.db-wal
# If very large, force checkpoint:
sqlite3 /var/lib/audiobooks/auth.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

## Monitoring and Alerting

### Recommended Health Checks

```bash
# Add to monitoring system (cron, Prometheus, etc.):

# 1. API health endpoint
curl -s -o /dev/null -w "%{http_code}" http://localhost:5001/api/health

# 2. Database accessible
sqlite3 /var/lib/audiobooks/auth.db "SELECT 1;" > /dev/null 2>&1

# 3. Key file exists and readable
test -r /var/lib/audiobooks/auth.key

# 4. Session cleanup running (check recent timestamps)
# Sessions older than 1 hour should not exist
sqlite3 /var/lib/audiobooks/auth.db \
  "SELECT COUNT(*) FROM sessions WHERE last_seen < datetime('now', '-1 hour');"
```

### Log Locations

| Component | Log Location |
|-----------|--------------|
| API | `journalctl -u audiobook-api` |
| Caddy | `journalctl -u caddy` |
| Auth events | API logs with `[AUTH]` prefix |
| Session cleanup | API logs with `[SESSION]` prefix |

## Backup Checklist

**Daily Backups Must Include:**
1. `/var/lib/audiobooks/auth.db` - User accounts, sessions
2. `/var/lib/audiobooks/auth.key` - Encryption key (CRITICAL)
3. `/etc/audiobooks/audiobooks.conf` - Configuration

**Backup Verification:**
```bash
# Test backup restore monthly:
mkdir /tmp/auth-restore-test
cp backup/auth.db backup/auth.key /tmp/auth-restore-test/

# Verify database opens with key
sqlite3 /tmp/auth-restore-test/auth.db \
  -cmd "PRAGMA key = \"x'$(cat /tmp/auth-restore-test/auth.key)'\"" \
  "SELECT COUNT(*) FROM users;"

rm -rf /tmp/auth-restore-test
```

## Emergency Procedures

### Complete Lockout Recovery

If all administrators are locked out:

```bash
# 1. Stop the API
systemctl stop audiobook-api

# 2. Create emergency admin account
./library/tools/auth_admin.py --create-admin emergency_admin

# 3. Start API
systemctl start audiobook-api

# 4. Log in as emergency_admin and fix the issue

# 5. Remove emergency account when done
./library/tools/auth_admin.py --delete-user emergency_admin
```

### Factory Reset (Last Resort)

**WARNING: All user data will be lost!**

```bash
# 1. Stop services
systemctl stop audiobook-api

# 2. Back up current state (for forensics)
cp /var/lib/audiobooks/auth.db /var/lib/audiobooks/auth.db.bak
cp /var/lib/audiobooks/auth.key /var/lib/audiobooks/auth.key.bak

# 3. Remove database and key
rm /var/lib/audiobooks/auth.db /var/lib/audiobooks/auth.key

# 4. Start API (creates fresh database)
systemctl start audiobook-api

# 5. Register new admin account through web UI
```
