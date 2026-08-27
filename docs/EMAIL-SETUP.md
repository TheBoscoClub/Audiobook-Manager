# Email Setup Guide

Audiobook-Manager sends email for a handful of user-facing flows — invitation links, activation messages, password-recovery flows, notifications, and admin replies to suggestions. This guide covers configuring outbound mail.

> **TL;DR**: the default configuration needs **no credential at all**. Every sender in this project submits to a local mail relay on `127.0.0.1:25` and lets that relay own the authenticated uplink. Set `SMTP_FROM` to an address your relay is allowed to send as, and you are done. `SMTP_USER` / `SMTP_PASS` are only for deployments that submit directly to a provider instead of running a relay.
>
> **Known defect**: three of the send sites still call `starttls()` unconditionally and therefore fail against a loopback relay. See the warning box below before relying on admin alerts or admin replies.

## The default: submit to a local relay, hold no credential

All five senders in this project share one contract — `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` — and all five default to `localhost` / `25` / no user / no password:

| Sender | Purpose |
|---|---|
| `scripts/email-report.py` | Translation-pipeline report mail |
| `library/backend/api_modular/auth_email.py` | Invitation, activation, recovery mail |
| `library/translation_monitor/notify.py` | Translation-monitor alerts |
| `library/auth/inbox_cli.py` | Operator CLI replies to suggestions |
| `library/auth/audit.py` | Security / audit notification mail |

Most send sites negotiate TLS and authenticate **only when both `SMTP_USER` and `SMTP_PASS` are non-empty**:

```python
with smtplib.SMTP(smtp_host, smtp_port) as server:
    if smtp_user and smtp_pass:
        server.starttls()
        server.login(smtp_user, smtp_pass)
    server.sendmail(...)
```

With no credential configured, the connection is a plain loopback submission — which is correct, because a loopback relay does not advertise `STARTTLS` and an unconditional `starttls()` raises against it.

> ### ⚠️ Three send sites are NOT guarded, and are broken on the relay
>
> The guard above is missing at three places, where `starttls()` is called
> unconditionally and only `login()` is gated:
>
> | Site | Function | Effect |
> |---|---|---|
> | `library/backend/api_modular/auth_email.py:182` | `_send_admin_alert` | Admin is never alerted to a new contact message |
> | `library/backend/api_modular/auth_email.py:213` | `_send_reply_email` | Admin replies to users are never delivered |
> | `library/auth/inbox_cli.py:181` | operator CLI reply | `audiobook-inbox reply` never sends |
>
> Measured against the live relay on `127.0.0.1:25`:
>
> ```text
> EHLO 250; advertised extensions:
>   8bitmime chunking dsn enhancedstatuscodes etrn pipelining size smtputf8 vrfy
> STARTTLS advertised?: False
> starttls() -> RAISES: SMTPNotSupportedError STARTTLS extension not supported by server.
> ```
>
> **The failure is silent.** Each site catches `Exception`, logs only the
> exception *class name* (`Failed to send admin alert: SMTPNotSupportedError`),
> and returns `False`; `auth.py:881` discards that return value. The user
> submitting a contact message sees success, and no mail is sent.
>
> Until these three are brought in line with the other send sites, treat admin
> alerts and admin replies as **non-functional on the relay path**. A deployment
> that submits directly to a provider with credentials configured is unaffected,
> because a provider on port 587 does advertise STARTTLS.

**Why this is the default.** The relay holds the provider API key in its own root-owned store (for Postfix, `/etc/postfix/sasl_passwd`), authenticates upstream over certificate-verified TLS, and queues across outages. The application holds nothing, so there is no credential in `audiobooks.conf`, none in the environment, none on the application's disk, and nothing to rotate when the provider key changes. A store-and-forward relay also means a provider outage delays mail instead of dropping it — `smtplib` returning cleanly only proves the relay accepted the message, so letting the relay own delivery is what makes that acceptance meaningful.

Reference configuration for the relay-backed default:

```bash
# /etc/audiobooks/audiobooks.conf
SMTP_HOST="127.0.0.1"
SMTP_PORT="25"
SMTP_USER=""                       # empty — no auth is attempted
SMTP_PASS=""                       # empty — no auth is attempted
SMTP_FROM="library@YOUR-DOMAIN"    # must be an envelope sender your relay accepts
```

Any MTA that accepts loopback submission works — Postfix, exim4, OpenSMTPD, or a null-client relay. Configuring the MTA itself is outside this project's scope.

### Verifying the relay path

The proof is the relay's own log, not the sending script's exit code. A queued message is not a delivered one:

```bash
sudo -u audiobooks python3 -c '
import smtplib
from email.message import EmailMessage
m = EmailMessage()
m["Subject"] = "Audiobook-Manager smoke test"
m["From"] = "library@YOUR-DOMAIN"
m["To"] = "recipient@example.com"
m.set_content("If you can read this, outbound mail is working.")
with smtplib.SMTP("127.0.0.1", 25) as s:
    s.send_message(m)
print("submitted")'

# Then confirm the uplink actually delivered it:
sudo journalctl -u postfix --since '-5 min' | grep -E 'status=(sent|bounced|deferred)'
mailq   # should be empty once the queue drains
```

Look for `status=sent` with the upstream provider's queue ID. `status=deferred` means the relay accepted it but could not hand it off — check the relay's credentials, not the application's.

## Transport security — 25 vs 587 vs 465

| Port | Mode | When to use | Notes |
|------|------|-------------|-------|
| **25** (`smtp`) | **Plaintext**, loopback only | **Default.** Submitting to a relay on `127.0.0.1` that owns the authenticated uplink. | Safe here precisely because the traffic never leaves the host. **Never use over the internet** — most providers reject port 25 submissions outright. |
| **587** (`submission`) | **STARTTLS** | Submitting directly to a provider with no local relay. Requires `SMTP_USER` + `SMTP_PASS`. | The only supported credentialed mode. |
| **465** (`smtps`) | **Implicit SSL/TLS** | **Not supported.** | The senders use `smtplib.SMTP` + `starttls()`; none constructs `smtplib.SMTP_SSL`, so a connection to 465 will hang or fail on the plaintext banner read. Use 587. |

There is no configuration key that forces or overrides transport mode. At the guarded send sites TLS is attempted if and only if a username and a password are both set, and only ever as STARTTLS. At the three unguarded sites listed above, STARTTLS is attempted unconditionally — which is precisely why they fail on port 25.

## Direct-to-provider setups (no local relay)

If the host has no MTA, the senders can submit straight to a provider. This is the mode that needs a credential, and the credential then lives on the application host — accept that tradeoff deliberately.

### Resend

Domain must be verified in the Resend console, with DNS records added at your DNS host:

```text
TXT  resend._domainkey.YOUR-DOMAIN   (DKIM public key from Resend)
MX   send.YOUR-DOMAIN                feedback-smtp.eu-west-1.amazonses.com  pri=10
TXT  send.YOUR-DOMAIN                v=spf1 include:amazonses.com ~all
```

Create a **send-only API key**, then:

```bash
SMTP_HOST="smtp.resend.com"
SMTP_PORT="587"
SMTP_USER="resend"
SMTP_PASS_FILE="/etc/audiobooks/smtp-pass"   # see below — prefer this to an inline value
SMTP_FROM="library@YOUR-DOMAIN"
```

### Gmail (personal account)

Gmail requires an **app-specific password** — the regular account password has not worked since 2022. Generate one at <https://myaccount.google.com/apppasswords>.

```bash
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_USER="your.address@gmail.com"
SMTP_PASS_FILE="/etc/audiobooks/smtp-pass"
SMTP_FROM="your.address@gmail.com"
```

Gmail rejects an `SMTP_FROM` that does not match `SMTP_USER` (or an alias verified in the account). Free Gmail has a **500 recipients/day** cap — fine for a small library, tight for a large multi-user deployment.

### Microsoft 365 / Outlook.com

```bash
SMTP_HOST="smtp.office365.com"
SMTP_PORT="587"
SMTP_USER="your.address@outlook.com"
SMTP_PASS_FILE="/etc/audiobooks/smtp-pass"
SMTP_FROM="your.address@outlook.com"
```

Enterprise tenants with MFA enforce app passwords, same as Gmail.

### Protonmail Bridge — do not use

**This project has no Bridge code path and no Bridge credential.** Bridge is not used for any mail this application sends, and the project reads no Bridge credential file.

Two independent reasons not to point the application at it:

1. Bridge wraps every outbound message in PGP/MIME (`multipart/signed; protocol="application/pgp-signature"`). Apple's mac.com / icloud.com servers reject that with `554 5.7.1 [CS01]`, so users on Apple mail clients silently do not receive invitation email.
2. Bridge decrypts its vault through the desktop session's secret service, so it is only running while someone is logged in. A service that must send mail unattended cannot depend on it — after an unattended reboot Bridge is simply down, and mail stops with no error the application can see.

Use a local relay (the default above) or a direct provider submission instead.

## Keeping the credential out of the config file

Every credential this project reads goes through `library/common_utils/secret_resolver.py`, which accepts either an inline value or a `*_FILE` pointer to a file containing it. Precedence is **inline value → `*_FILE` pointer → empty**; a non-empty inline value always wins.

```bash
# In /etc/audiobooks/audiobooks.conf
SMTP_PASS_FILE="/etc/audiobooks/smtp-pass"
# (leave SMTP_PASS unset or commented — an inline value would win over the pointer)
```

You create and populate the target yourself:

```bash
printf '%s\n' 'YOUR_API_KEY' | sudo tee /etc/audiobooks/smtp-pass >/dev/null
sudo chown audiobooks:audiobooks /etc/audiobooks/smtp-pass
sudo chmod 600 /etc/audiobooks/smtp-pass
```

> **Note**: `install.sh` / `upgrade.sh` no longer create an `smtp-pass` stub. The only credential stub they create automatically is `auth.key` (see `OPTIONAL_CREDENTIAL_FILES` in `scripts/install-manifest.sh`). This changed when mail moved to the relay — the default configuration holds no SMTP credential, so a zero-byte stub for one was pure residue.

The same pointer pattern works for `AUDIOBOOKS_DEEPL_API_KEY_FILE` and `AUTH_KEY_FILE`.

### A hand-copied credential file does not self-update

The canonical store for a credential is wherever the operator actually manages it. Any copy on the application host — `/etc/audiobooks/smtp-pass` included — is exactly that: a copy. When the key is rotated in the canonical store, the copy keeps serving the dead credential, and nothing reports the drift until mail stops.

If the host has the operator tool `/usr/bin/derive-service-secret` (its canonical source lives **outside this project**; this project only consumes it), `install.sh` / `upgrade.sh` install a systemd drop-in at `/etc/systemd/system/audiobook-api.service.d/derive-secrets.conf` from the template `systemd/audiobook-api-derive-secrets.conf.example`. On hosts without the tool the drop-in is skipped with an informative message and nothing else changes.

With the drop-in active, at every service start `derive-service-secret` runs as root from `ExecStartPre=+`, reads one named variable from the operator's canonical credential store, and writes it to `/run/audiobooks/<name>` as a `0400 audiobooks:audiobooks` file. Because `RuntimeDirectory=audiobooks` makes systemd wipe and recreate `/run/audiobooks` on every start, the secret is re-derived each time and drift is structurally impossible. The tool fails closed — the service does not start if the named variable is missing or empty.

**No SMTP secret is derived by default**, because the default mail path has no SMTP secret. The drop-in ships with one derive line, for the Cloudflare cache-purge token:

```bash
# In /etc/audiobooks/audiobooks.conf, repoint the pointer at the /run path:
CLOUDFLARE_PURGE_TOKEN_FILE="/run/audiobooks/cloudflare-purge-token"
```

A deployment that does submit directly to a provider can add its own `SMTP_PASS` derive line to the drop-in and point `SMTP_PASS_FILE` at `/run/audiobooks/smtp-pass`.

**The application's contract is unchanged** either way: it only ever reads `SMTP_PASS` / `SMTP_PASS_FILE`. The drop-in changes only where the pointer target gets its content.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `SMTPNotSupportedError: STARTTLS extension not supported` | A credential is configured while submitting to a loopback relay that does not advertise STARTTLS | Clear `SMTP_USER` and `SMTP_PASS` — the relay path is credential-less by design |
| Connection hangs, or `SMTPServerDisconnected` on connect | `SMTP_PORT="465"` — implicit TLS is not supported | Use 587 with STARTTLS, or 25 to a local relay |
| Mail "sends" but never arrives, `mailq` non-empty | Relay accepted the message but its own uplink is failing | Check the relay's credentials and `journalctl -u postfix`, not the application config |
| `535 Authentication credentials invalid` | Account password used where an app password / API key is required | Generate an app password or API key in the provider console |
| `421 4.7.0 Try again later` from Gmail | Daily sending cap hit (500/day personal) | Wait 24 h, or move to a relay / Workspace / Resend |
| `554 5.7.1 [CS01]` from Apple mail | PGP/MIME-wrapped message (Protonmail Bridge) | Do not submit through Bridge — see above |
| Emails silently missing from inbox | SPF / DKIM failure → recipient spam folder | Verify DNS records (TXT `v=spf1 ...`, DKIM selector) for the domain in `SMTP_FROM` |

## Related

- `/etc/audiobooks/audiobooks.conf` — canonical `SMTP_*` configuration
- `library/common_utils/secret_resolver.py` — the `*_FILE` pointer implementation
- `systemd/audiobook-api-derive-secrets.conf.example` — derive-service-secret drop-in template
- `scripts/install-manifest.sh` — `OPTIONAL_CREDENTIAL_FILES`, the single definition site for auto-created credential stubs
