# Development TLS Certificates

The `server.crt` and `server.key` in this directory are **self-signed,
dev-only TLS certificates** used by the local development API server
(`library/backend/api_modular/`) and the development proxy.

## Security

- **This directory is gitignored** (`.gitignore` contains `library/certs/`).
  No cert or key in this directory will ever be committed or pushed.
- The private key is dev-only and rotated per developer install. It has
  no production value and is never deployed to production, QA, or test
  environments — those environments receive proper certificates from the
  install/upgrade flow.
- Production certificates live at the canonical install paths managed by
  `install.sh` (see `/etc/audiobooks/` for production config paths).

## Regenerating

If the dev cert is missing or expired, the `scripts/` toolchain regenerates
it automatically on the next `launch.sh` or equivalent dev-server start.
You can also regenerate manually:

```bash
openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
    -keyout library/certs/server.key \
    -out library/certs/server.crt \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
chmod 600 library/certs/server.key
```

## Never Commit

If you see any `.key` or `.pem` file in this directory appear in
`git status`, **do not commit it**. Confirm `.gitignore` still excludes
`library/certs/` and investigate why the file became tracked.
