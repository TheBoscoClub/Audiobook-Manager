# Security Policy

---

## Reporting Security Vulnerabilities

If you discover a security vulnerability in this project, please report it privately:

1. **Do NOT open a public issue**
2. Use GitHub's private vulnerability reporting (if enabled)
3. Email the maintainer directly
4. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if available)

## Security Considerations

### Sensitive Data Protection

**NEVER commit sensitive data to the repository.**

**Safe practices:**

- ✅ Use environment variables for secrets
- ✅ Keep `.gitignore` up to date
- ✅ Use config templates (`.example` files)
- ❌ Never hardcode credentials
- ❌ Never commit API keys, passwords, or tokens

### Protected Files

The following types of files should NEVER be committed (add to `.gitignore`):

- Configuration files with credentials
- API keys or tokens
- Personal data files
- Logs that may contain sensitive information
- Backup files containing sensitive data

### Code Review Requirements

All pull requests must:

1. Not modify `.gitignore` to expose sensitive files
2. Not add code that logs or transmits credentials
3. Not add code that exfiltrates sensitive data
4. Not introduce dependencies with known vulnerabilities
5. Maintain security best practices

### Prohibited Changes

The following changes will be **rejected**:

❌ **Removing or weakening `.gitignore` entries**
❌ **Logging sensitive data** (credentials, tokens, personal info)
❌ **Transmitting data to unauthorized endpoints**
❌ **Storing credentials in code**
❌ **Disabling security features**

### Secure Contribution Guidelines

**Before submitting a PR:**

1. **Review your changes for sensitive data:**

   ```bash
   git diff | grep -iE "(api.?key|password|token|secret|auth)"
   ```

2. **Verify `.gitignore` is intact:**

   ```bash
   git status --ignored
   ```

3. **Check for hardcoded credentials:**

   ```bash
   grep -r "API_KEY=" . --include="*.sh" --include="*.py" --include="*.js"
   ```

4. **Run local security check:**

   ```bash
   # Ensure no sensitive files are staged
   git diff --cached --name-only
   ```

### Dependency Security

**Check for vulnerabilities regularly:**

**Python:**

```bash
pip install safety && safety check
# Or: pip install pip-audit && pip-audit
```

**Node.js:**

```bash
npm audit
# Or: yarn audit
```

**Update system packages:**

Use your system's package manager to keep packages up to date:

| OS | Update Command |
|----|----------------|
| Arch/CachyOS/Manjaro | `sudo pacman -Syu` |
| Ubuntu/Debian | `sudo apt update && sudo apt upgrade` |
| Fedora/RHEL | `sudo dnf upgrade` |
| openSUSE | `sudo zypper update` |
| macOS (Homebrew) | `brew update && brew upgrade` |
| Alpine | `apk update && apk upgrade` |

**Update dependencies regularly:**

```bash
# Python: pip list --outdated
# Node.js: npm outdated
# Check your package manager's outdated list
```

### Local Security

**Protect your environment:**

```bash
# Secure config files
chmod 600 config/sensitive-file.conf

# Secure directories with sensitive data
chmod 700 sensitive-directory/
```

**Verify .gitignore is working:**

```bash
git check-ignore -v sensitive-file.conf
```

## GitHub Security Settings

### Enforced Settings (Repository Owner)

These describe how `main` is actually configured, not an aspiration. Verify with
`gh api repos/TheBoscoClub/Audiobook-Manager/branches/main/protection`.

**Branch Protection Rules on `main`:**

1. **Required status checks — 12 contexts, `strict: true`.** No pull request can
   merge until all of these report success, and the branch must be up to date
   first: `Python Tests (3.12/3.13/3.14)`, `Docker Build Check`, `ShellCheck`,
   `YAML Validation`, `ESLint (web-v2)`, `Ruff Linting`, `Type Checking`,
   `Security Scan (Bandit)`, `Dependency Vulnerabilities`, `security-scan`.
   This list must stay in sync with the job `name:` values in `ci.yml`,
   `python-security.yml` and `security-checks.yml` — a required context that no
   workflow ever produces blocks every PR permanently.
2. **Signed commits required.**
3. **Force pushes and branch deletion blocked.**
4. **Conversation resolution required before merging.**
5. **Pull request reviews are deliberately NOT required.** This is a
   solo-maintainer repository; a review requirement approves nothing and only
   produces a "Bypassed rule violations" notice on every direct push. Merge
   safety here comes from the required status checks in (1), which apply to
   bot-authored pull requests — including Dependabot's — because
   `enforce_admins` is off for the maintainer but the `GITHUB_TOKEN` used by
   `dependabot-auto-merge.yml` has no such exemption.

**Why (1) is load-bearing:** between 2026-08-11 and 2026-08-25 this repository
had required status checks *enabled with an empty context list*. `gh pr merge
--auto` therefore merged each Dependabot pull request the instant it was
approved, with CI red. Sixteen commits landed that way, one of which
(`mando>=0.8.2,<0.9`, conflicting with radon's `mando<0.8` ceiling) made
`requirements-dev.txt` unresolvable and left `main` unable to run its own type
checks for two weeks. Do not empty this list.

**Repository Settings:**

- ✅ Enable vulnerability alerts (Dependabot)
- ✅ Enable automated security fixes
- ✅ Enable private vulnerability reporting
- ✅ Review access permissions regularly

**Scheduled re-validation:** `ci.yml` and `security-checks.yml` both run daily
at 05:00 UTC in addition to push/PR, and `python-security.yml` runs weekly.
This is not redundancy. GitHub does not start workflow runs for pushes made
with `GITHUB_TOKEN`, so an auto-merged pull request lands on `main` without
re-triggering the push-event workflows; without a schedule, `main` can stop
being tested while every visible check stays green. Do not remove these
schedules on the grounds that push coverage already exists.

## Security Checklist for Contributors

Before submitting a PR, verify:

- [ ] No API keys, tokens, or passwords in code
- [ ] No hardcoded sensitive data
- [ ] No sensitive data in commit messages
- [ ] `.gitignore` not modified to expose sensitive files
- [ ] No new external API calls without discussion
- [ ] Dependencies checked for vulnerabilities
- [ ] Code doesn't log sensitive information
- [ ] Documentation updated if security-relevant changes

## Security Checklist for Maintainers

When reviewing PRs:

- [ ] Verify no sensitive data committed
- [ ] Check for malicious code patterns
- [ ] Review all file modifications carefully
- [ ] Verify `.gitignore` changes (if any)
- [ ] Check for data exfiltration attempts
- [ ] Review new dependencies
- [ ] Verify error handling doesn't expose secrets
- [ ] Check logging statements for sensitive data
- [ ] Run code locally before merging

## Removing Sensitive Data from Git History

If you accidentally commit sensitive data:

```bash
# Using BFG Repo-Cleaner (recommended)
bfg --delete-files sensitive-file.conf
git reflog expire --expire=now --all && git gc --prune=now --aggressive

# Force push (only if you're sure!)
git push --force --all
```

**Then immediately:**

1. Revoke the exposed credential
2. Generate new credentials
3. Update your environment

## Regular Security Maintenance

### Monthly

- [ ] Review dependencies for updates
- [ ] Check for security advisories
- [ ] Review access logs (if available)

### Quarterly

- [ ] Security audit of codebase
- [ ] Review and update `.gitignore`
- [ ] Review branch protection rules

### Annually

- [ ] Comprehensive security review
- [ ] Update security documentation
- [ ] Review threat model

## Contact

For security concerns, contact the maintainer through GitHub.

---

**Last Updated:** 2026-08-05
**Version:** 8.4.2.1
