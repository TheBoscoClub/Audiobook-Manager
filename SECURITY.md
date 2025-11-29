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
```bash
# Arch Linux / CachyOS / Manjaro
sudo pacman -Syu

# Ubuntu / Debian
sudo apt update && sudo apt upgrade

# Fedora / RHEL
sudo dnf upgrade

# macOS (Homebrew)
brew update && brew upgrade
```

**Update dependencies regularly:**
```bash
# Python: pip list --outdated
# Node.js: npm outdated
# Arch: pacman -Qu
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

### Recommended Settings (Repository Owner)

**Branch Protection Rules:**
1. Require pull request reviews before merging
2. Require status checks to pass
3. Require conversation resolution before merging
4. Restrict who can push to protected branches

**Repository Settings:**
- ✅ Enable vulnerability alerts (Dependabot)
- ✅ Enable automated security fixes
- ✅ Enable private vulnerability reporting
- ✅ Review access permissions regularly

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

**Last Updated:** 2025-11-28
**Version:** 1.0
