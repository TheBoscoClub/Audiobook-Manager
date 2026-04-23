# Email Setup Guide

Audiobook-Manager sends email for a handful of user-facing flows — invitation links, activation messages, password-recovery flows, notifications, and admin replies to suggestions. This guide covers configuring outbound SMTP end-to-end for the major provider options + transport security tradeoffs.

> **TL;DR**: set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` in `/etc/audiobooks/audiobooks.conf`. The app detects transport security automatically by port (465 → implicit SSL, 587 → STARTTLS, 25 → plaintext fallback).

## Transport security — 465 vs 587 vs 25

| Port | Mode | When to use | Notes |
|------|------|-------------|-------|
| **587** (`submission`) | **STARTTLS** — plaintext handshake upgraded to TLS | **Default recommendation.** Every major provider (Resend, Gmail, Outlook, Protonmail Bridge) supports this. | Most operators should use 587 unless the provider explicitly requires 465. |
| **465** (`smtps`) | **Implicit SSL/TLS** — connection is encrypted from byte zero | Older providers that never implemented STARTTLS properly, or for paranoid "never allow a plaintext handshake" setups. | Historically deprecated, then un-deprecated by RFC 8314 because STARTTLS downgrade attacks are a real concern. |
| **25** (`smtp`) | **Plaintext** (no TLS) — relay only | Localhost-only relay on a trusted host, or mail-submission-agents that handle TLS themselves. | **Never use over the internet.** Most providers reject submissions on port 25 entirely now. |

The app's SMTP client negotiates TLS on the first SMTP response that includes `STARTTLS` capability. If you need to force behavior, set `SMTP_TLS` in `audiobooks.conf`:

```bash
SMTP_TLS="starttls"    # default: opportunistic STARTTLS on any port
SMTP_TLS="implicit"    # port 465-style: wrap the socket in TLS from connect
SMTP_TLS="none"        # plaintext; ONLY for localhost relays
```

## Provider recipes

### Resend (recommended for thebosco.club)

**Why it's the right choice** for this project: cleanroom SMTP service via Amazon SES, no PGP/MIME wrapping (Protonmail Bridge adds `multipart/signed` which Apple's mac.com rejects with `554 5.7.1 [CS01]`), free tier (3,000 emails/month) covers invitation + activation email without additional spend.

Domain must be verified in Resend console + DNS records added to Cloudflare / your DNS host:

```
TXT  resend._domainkey.YOUR-DOMAIN   (DKIM public key from Resend)
MX   send.YOUR-DOMAIN                feedback-smtp.eu-west-1.amazonses.com  pri=10
TXT  send.YOUR-DOMAIN                v=spf1 include:amazonses.com ~all
```

Create a **send-only API key** in the Resend console, then:

```bash
# /etc/audiobooks/audiobooks.conf
SMTP_HOST="smtp.resend.com"
SMTP_PORT="587"
SMTP_USER="resend"
SMTP_PASS="re_DJR8Prne_SAMPLE_SEND_ONLY_KEY"
SMTP_FROM="library@YOUR-DOMAIN"
```

Verify:

```bash
systemctl --user status protonmail-bridge     # should be inactive/removed if swapping away
sudo -u audiobooks /opt/audiobooks/scripts/email-test-send.sh recipient@example.com
```

### Gmail (personal account)

Gmail requires an **app-specific password** — regular account password won't work since 2022. Generate at <https://myaccount.google.com/apppasswords>.

```bash
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_USER="your.address@gmail.com"
SMTP_PASS="xxxx xxxx xxxx xxxx"   # 16-char app password, no spaces in conf
SMTP_FROM="your.address@gmail.com"
```

Gmail rejects `SMTP_FROM` that doesn't match `SMTP_USER` (or an alias verified in the account). Free Gmail has a **500 recipients/day** throughput cap — fine for a small audiobook library, tight for a large multi-user deployment.

### Microsoft 365 / Outlook.com

```bash
SMTP_HOST="smtp.office365.com"
SMTP_PORT="587"
SMTP_USER="your.address@outlook.com"
SMTP_PASS="account-password-OR-app-password"
SMTP_FROM="your.address@outlook.com"
```

Enterprise tenants with MFA enforce app passwords — same as Gmail. Personal Outlook.com accounts accept the regular password as of 2026-Q1 but Microsoft has been threatening to remove this; prefer app password.

### Protonmail Bridge (NOT recommended for user-facing email)

**Protonmail Bridge wraps every outbound message in PGP/MIME** (`multipart/signed; protocol="application/pgp-signature"`). Apple's mac.com / icloud.com mail servers reject this with `554 5.7.1 [CS01]`. Users on Apple mail clients will silently NOT receive invitation emails.

If you still need it (e.g., internal-only mail where recipients are on Protonmail too):

```bash
SMTP_HOST="127.0.0.1"
SMTP_PORT="1025"           # Bridge listens on this locally
SMTP_USER="you@pm.me"
SMTP_PASS="bridge-generated-password"
SMTP_FROM="you@pm.me"
SMTP_TLS="starttls"
```

Start the Bridge user-level service:

```bash
systemctl --user status protonmail-bridge
systemctl --user enable --now protonmail-bridge
```

### Generic SMTP relay (postfix, exim4, etc.)

For hosts running their own MTA that handles outbound relay:

```bash
SMTP_HOST="127.0.0.1"
SMTP_PORT="25"
SMTP_USER=""              # no auth for localhost relay
SMTP_PASS=""
SMTP_FROM="library@YOUR-DOMAIN"
SMTP_TLS="none"           # localhost plaintext is fine; MTA handles TLS upstream
```

### mailx / s-nail (CLI tools for testing only)

These aren't a production SMTP backend — they're useful for operator smoke-tests:

```bash
echo "Test body" | mailx -s "Subject" -S smtp=smtp.resend.com:587 \
  -S smtp-auth=login -S smtp-auth-user=resend \
  -S smtp-auth-password=re_XXX -S from=library@YOUR-DOMAIN \
  -S smtp-use-starttls recipient@example.com
```

## Operator smoke test

A quick test script (optional convenience):

```bash
# scripts/email-test-send.sh
#!/bin/bash
set -euo pipefail
# shellcheck source=/dev/null
source /etc/audiobooks/audiobooks.conf
: "${1?Usage: $0 recipient@example.com}"
python3 - <<PY
import smtplib
from email.message import EmailMessage
msg = EmailMessage()
msg["Subject"] = "Audiobook-Manager email smoke test"
msg["From"] = "$SMTP_FROM"
msg["To"] = "$1"
msg.set_content("If you can read this, outbound SMTP is working.")
with smtplib.SMTP("$SMTP_HOST", $SMTP_PORT) as s:
    s.starttls()
    s.login("$SMTP_USER", "$SMTP_PASS")
    s.send_message(msg)
print("sent to $1")
PY
```

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `554 5.7.1 [CS01]` from Apple mail | Protonmail Bridge PGP/MIME wrap | Switch to Resend or another cleanroom SMTP |
| `535 Authentication credentials invalid` | Using account password where app password is required | Generate an app password in provider console |
| `421 4.7.0 Try again later` from Gmail | Daily sending limit hit (500/day personal) | Wait 24h, or migrate to Workspace/Resend |
| `connect: No route to host` | `SMTP_HOST` DNS mismatch | `dig $SMTP_HOST` to verify resolution |
| `ssl.SSLError: UNSUPPORTED_PROTOCOL` | Port 465 used with `SMTP_TLS="starttls"` | Set `SMTP_TLS="implicit"` for port 465 |
| Emails silently missing from inbox | SPF / DKIM failure → recipient spam folder | Verify DNS records (TXT `v=spf1 ...`, DKIM selector) |

## Related

- `/etc/audiobooks/audiobooks.conf` — canonical SMTP_* configuration
- `scripts/email-test-send.sh` (optional) — operator smoke-test wrapper
- `~/.claude/rules/infrastructure.md §Protonmail Bridge` — why the project migrated away from Bridge for user-facing mail
